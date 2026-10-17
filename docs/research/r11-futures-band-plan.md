# R11: R7 futures long/short with a ±2% hysteresis band (pre-declared)

Written 2026-10-16, before any R11 backtest was run. R10a (path A spot with a ±2% band around
SMA100) passed its screen on 2022–2025. Note asked whether to try the same band on R7 (BTC perp,
long above / short below daily SMA100) and chose "เริ่มเลย". Exploratory, on data already seen;
C1 and C2 are not used.

## Question

Does the R10a band reduce R7's whipsaw (each SMA100 cross flips long ↔ short, double-size trades)
enough to improve R7's risk-adjusted result?

Hypothesis: fewer flips save fees and wrong-way shorts in sideways phases. Counter-risk, declared
now: the band does not fix that R7's short days lost money overall (log −0.078 on development), and
later flips can cost in sharp moves.

## Data

`data/binance_um` (BTCUSDT USD-M perp 1h klines and funding, 2022-01-01 → 2025-12-31,
checksum-verified), the files path A and R8–R10 used. No 2020–2021 or 2026 bars are loaded, so the
band state cannot depend on C1 or C2.

## Rules (one trial)

Everything is R7 (`research/spike/run_r7.py`: daily bars, next-open execution, 1x, taker 0.05% +
2 bps per side, daily funding, liquidation check, 10,000 USDT), except the side:

- SMA100 of daily closes. On the first day with a valid SMA100, side = +1 if close > SMA100, −1 if
  close < SMA100, 0 if equal.
- After that, each day: if side ≠ +1 and close > SMA100 × 1.02 → +1 (long); if side ≠ −1 and
  close < SMA100 × 0.98 → −1 (short); otherwise keep the previous side. Invalid days keep it.

R7 itself is re-run on the same data as the base for comparison (it should reproduce its
development result, +103.4% / Sharpe 0.43 / DD 55.0%; any difference is reported).

## Window

R7's development set, 2022-04-12 → 2026-01-01. Also reported per year (2022 from April, 2023,
2024, 2025).

## Pass criteria

1. **Better than R7:** Sharpe(R11) > Sharpe(R7) **and** max DD(R11) ≤ max DD(R7) **and** net > 0.
2. Also reported against R7's own rule (net > 0 and Sharpe > buy & hold) and Note's objective.

Also reported: flips, days long/short, fees, funding, long-day vs short-day log return, results
with costs ×2. Passing makes R11 a candidate for a confirmation run (Note's separate approval) and
a futures paper book; it does not change the running futures-r7 book. A fail is recorded and not
retuned.

## Result (run once, 2026-10-16, `research/spike/run_r11.py`; outputs in `/mnt/project-files/r11-results/`)

The R7 re-run on `data/binance_um` reproduced its development result exactly (+103.4% / 0.43 /
55.0%, 57 flips, fees 904, funding 3,478).

2022-04-12 → 2026-01-01, 10,000 USDT, taker 0.05% + 2 bps per side, daily funding:

| | Net | Sharpe | Max DD | Flips | Days long / short | Fees | Funding paid | Log ret long / short days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R7 | +103.4% | 0.428 | 55.0% | 57 | 757 / 603 | 904 | 3,478 | +0.788 / −0.078 |
| R11 band ±2% | +221.0% | 0.705 | 39.4% | 21 | 768 / 592 | 456 | 4,570 | +1.018 / +0.148 |
| BTC buy & hold | +121.5% | 0.424 | 62.0% | – | – | – | – | – |

By year (net / Sharpe / max DD): R7 +34.6% / 1.01 / 21.1%, +23.9% / 0.48 / 43.7%, +10.5% / 0.19 /
55.0%, +10.4% / 0.27 / 27.9%; R11 +43.3% / 1.26 / 16.2%, +31.7% / 0.62 / 35.9%, +54.4% / 0.82 /
39.4%, +10.1% / 0.26 / 35.2% (2022 Apr–Dec / 2023 / 2024 / 2025). With costs ×2: R7 0.38 / 56.1%,
R11 0.69 / 40.0% (Sharpe / max DD). No liquidation.

**Verdict: R11 passes** criterion 1 (Sharpe 0.705 > 0.428, max DD 39.4% ≤ 55.0%, net > 0) and R7's
own rule against buy & hold (0.705 > 0.424). With the band the short days made money (+0.148 log)
instead of losing (−0.078), since fewer flips meant fewer wrong-way shorts. 2025 was no better than
R7 (+10.1% vs +10.4%, max DD 35.2% vs 27.9%). It is still behind the spot band R10a on risk
(Sharpe 1.00, max DD 26.0% over a near-identical window), and pays more funding.

Same caveats as R10: the window was seen before and the band came from the 2025 analysis, so R11 is
a candidate for confirmation, not evidence. C1 and C2 stay unspent; the futures-r7 book is unchanged.
