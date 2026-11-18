# R15: futures strategy search on BTCUSDT perp (pre-declared)

Written 2026-11-17, **before any R15 backtest was run**. Goal (platform plan v0.2, CP6): find a
BTCUSDT USDⓈ-M perp strategy that beats both benchmarks in backtest, or report honestly that none
does. Note's question (2026-11-17): what leverage, how often to trade, and can one decision per day
win? So every variant decides **once per UTC day** (at the daily close, filled at the next open), and
the directional variants are run at 1x, 2x and 3x.

## What we already know

- R7 (long/short on SMA100) and R11 (R7 + ±2% band) lost to buy & hold on C1. R11's dev pass was
  helped by a band taken from the 2025 analysis.
- R10a (spot, long/cash, ±2% band, half size when funding is hot) is the best so far:
  dev 2022-05 → 2025 Sharpe 1.00 / DD 26.0%, C1 Sharpe 1.93 / DD 23.2%.
- Vol targeting (R9b/R12), ADX/ATR filters (R14) and H1 tight-stop EAs (R13) did not help.

## Variants (eight runs, nothing else; nothing tuned afterwards)

Common: features known at day d close; target decided at d close, traded at d+1 open. Perp taker
0.05% + 2 bps slippage per side, spot 0.10% + 2 bps. Funding: each day the perp position pays
`perp_qty × daily close × sum of that day's funding rates` (short receives). 10,000 USDT, one
account (spot leg + perp margin share the cash). Quantities are not rounded.

Rebalance rule (all perp variants): a leg is traded at the open only when its target changed since
the previous day, or its actual weight (notional / equity, signed) differs from the target by more
than 20% of the target (min 0.05 absolute). This stops leverage from drifting far while avoiding
daily small trades.

Liquidation (perp leg, conservative, isolated): if at the day's worst 1h price (low for long, high for
short) account equity would fall to the maintenance margin (0.5% of perp notional) or below, the
book is liquidated at that price, equity drops to that level, and the run stops (reported as ruin).

1. **F1 gated long/short, L = 1, 2, 3 (three runs).** R10a band state on SMA100 (in > SMA×1.02,
   out < SMA×0.98). Long when the band state is in: weight +L × (0.5 if funding hot else 1), the same
   funding rule as R10a. Short only when the band state is out **and** SMA100 is falling (SMA100
   today < SMA100 20 days ago): weight −L. Otherwise flat. Hypothesis: R7/R11 shorts lost in
   sideways phases; shorting only in a falling trend keeps the bear-market gain.
2. **F2 cash-and-carry (one run).** Long spot + short perp, equal quantity, each leg 0.5 × equity
   (delta-neutral; perp leg at 1x on its margin). ON when the 7-day mean funding > 0.005% per 8h
   (0.015%/day); OFF when the 7-day mean < 0. Between the two, keep the previous state. Legs are
   rebalanced back to 0.5/0.5 when the spot leg leaves 40–60% of equity. Hypothesis: positive
   funding is a premium that longs pay; harvesting it gives a low-risk return.
3. **F3 R10a + carry on idle cash (one run).** R10a unchanged on spot (weight w ∈ {0, 0.5, 1}).
   The idle share (1 − w) runs F2's carry when F2's state is ON: extra spot 0.5 × (1 − w) and perp
   short 0.5 × (1 − w). Hypothesis: R10a sits in cash about half the time, often when funding is
   still positive; the futures leg makes that cash earn.
4. **F4 trend ensemble, vol-scaled, L = 1, 2, 3 (three runs).** s = mean of sign(30-, 60- and
   90-day close return) ∈ {−1, −⅓, ⅓, 1}. Weight = s × min(1, 0.40 / vol30) × L, where vol30 is the
   annualised std of 30 daily log returns. Classic time-series momentum; lookbacks are the common
   1/2/3-month set, chosen so warm-up fits before the window start without loading C1 bars.

Leverage note (declared now): leverage scales return and drawdown together; it cannot raise Sharpe,
and funding and fees grow with it. 2x/3x are run to answer Note's question, and are expected to fail
the drawdown condition.

## Benchmarks (same window, same code)

- **B&H:** buy BTC spot once at the first open, 0.10% + 2 bps, hold.
- **R10a spot:** as `run_r10.py` (perp prices as proxy, spot costs), reproduced first
  (dev: +244.8% / 1.00 / 26.0%). Also run on spot prices in the R15 simulator; any difference is
  reported.

## Data

- dev: perp 1h klines + funding 2022-01-01 → 2025-12-31 (`data/binance_um`, already used by R10),
  spot 1d klines 2022-01 → 2025-12 (`data/binance_spot`), all checksum-verified.
- C1: same files for 2020-01 → 2021-12 only (separate directories). **No 2026 file is downloaded;
  C2 (2026-01 → 2026-10) is not used.**

## Windows and order

1. **dev 2022-05-01 → 2026-01-01** (R10's window, seen many times). Run all eight; commit results.
2. **C1 2020-04-15 → 2022-01-01**, run once for all eight (no selection). C1 was spent on R10a/R11,
   so it is a second screen for new rules, not a clean confirmation. It contains March 2020 (−40%
   intraday), a real liquidation test for 2x/3x.

## Pass (per variant, per window, base costs)

- **A, beats R10a:** Sharpe > Sharpe(R10a) **and** max DD ≤ max DD(R10a) + 5 points.
- **B, beats B&H (Note's objective):** net > 0 **and** (Sharpe > Sharpe(B&H) **or** max DD ≤ ½ B&H's).
- **C, robust to costs:** with costs ×2, B still holds.
- No liquidation.

"Winner" = A, B, C and no liquidation on **both** dev and C1. Eight runs make a lucky pass more
likely, so a winner is a candidate for a forward paper book (and C2 only with Note's separate OK),
not proof. F2 is judged on the same rules; its absolute return is also reported, since a high
Sharpe on a small return may not be what Note wants.

## Reported

Net, CAGR, Sharpe, max DD (daily closes) and worst intraday DD, per year, trades, days long/short/
flat, fees, funding paid/received, liquidation, costs ×2, correlation of daily returns with R10a.
Outputs: `/mnt/project-files/r15-results/`. A fail is recorded, not retuned.

## Dev result (run once 2026-11-17 at the runner commit; `/mnt/project-files/r15-results/dev/`)

2022-05-01 → 2026-01-01. R10a reproduced exactly (+244.8% / 1.00 / 26.0%); B&H +132.6% / 0.46 / 60.2%.

| Run | Net | Sharpe | Max DD | Pass |
| --- | --- | --- | --- | --- |
| F1 gated L/S 1x / 2x / 3x | +266% / +463% / +361% | 0.74 / 0.51 / 0.30 | 38.9% / 65.5% / 84.0% | no (A) |
| F2 carry | +12.5% (3.3%/yr) | 8.39 | 0.4% | yes |
| F3 R10a + carry | +251.6% | 1.01 | 25.6% | yes (by a hair) |
| F4 TSMOM 1x / 2x / 3x | +99% / +144% / +106% | 0.55 / 0.36 / 0.19 | 38.4% / 63.2% / 78.7% | no |

No liquidation at any leverage on dev. C1 is run next for all eight, unchanged.

## C1 result (run once 2026-11-17, `/mnt/project-files/r15-results/c1/`)

2020-04-15 → 2022-01-01. R10a reproduced (+443.0% / 1.93 / 23.2%); B&H +572.1% / 1.54 / 53.1%.

| Run | Net | Sharpe | Max DD | Pass |
| --- | --- | --- | --- | --- |
| F1 gated L/S 1x / 2x | +168% / +316% | 0.99 / 0.74 | 48.2% / 75.2% | no |
| F1 gated L/S 3x | **liquidated 2021-07-26** (short squeeze) | – | 100% | no |
| F2 carry | +23.7% (13.2%/yr) | 7.43 | 1.2% | yes |
| F3 R10a + carry | +489.4% | 2.02 | 22.8% | yes |
| F4 TSMOM 1x / 2x / 3x | +117% / +263% / +404% | 1.28 / 1.08 / 0.89 | 17.9% / 33.9% / 48.1% | no (A) |

## Verdict

- **Winners by the pre-declared rule: F2 and F3.** Neither is a directional futures strategy: both
  use the perp only as a short hedge to collect funding.
  - F2 alone earns little (3.3%/yr on dev, 13.2%/yr in the 2020–21 bull), roughly cash-like yield
    with a very smooth curve. Its huge Sharpe is mostly because the return is small and steady.
  - F3 beats R10a by a small margin: dev +251.6% vs +244.8% (Sharpe 1.01 vs 1.00), C1 +489% vs
    +443% (2.02 vs 1.93). Gain = idle cash earning funding. Correlation with R10a 1.00.
- **No directional perp strategy (long/short, any leverage) beats spot R10a** on either window. F1/F4
  at 1x pay funding and lose Sharpe; 2x/3x only scale drawdown (dev DD 65–84%), and 3x F1 was
  liquidated on C1.
- Post-hoc diagnostic (not pre-declared, not a candidate): F1's signal with the short legs removed
  (long-only perp 1x) gives dev 0.83 / 27.5% and C1 1.59 / 25.2%, versus long+short 0.74 / 38.9% and
  0.99 / 48.2%. Shorting added raw return on dev but lowered Sharpe and raised DD on both windows;
  long-only perp is still below spot R10a because of funding.
- Eight runs on a seen dev window plus a spent C1: F3 is a candidate for a forward paper book, not
  proof. C2 is unused.
