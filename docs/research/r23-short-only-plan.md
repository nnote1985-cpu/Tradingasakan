# R23: a standalone short-only BTC perp strategy, 1x vs 2x (pre-declared)

Written 2026-11-18, before any R23 backtest was run. Note asked (2026-11-18) for a strategy that only ever
shorts the BTC perp: no spot, no long. Goal: total P&L positive with a low drawdown. It is judged on its
own terms (benchmark = cash, 0%), **not** against R10a. Note also asked to compare 1x with 2x.

Earlier short work (R15 F1 short leg, R16 reversed systems, R17 short sleeve) only tested shorts as a
part of a long strategy, or only inside one regime. R23 tests short-only rules on their own.

## Honesty note on data (overfitting risk)

All BTC data used here (2018–2025) has been looked at before, and we know where the bear markets were
(2018, mid 2019 → Mar 2020, May–Jul 2021, 2022). Any short rule designed on it can look better than it
will be. So: one fixed grid declared here, fixed selection criteria, a robustness check over the whole
family, and one run on ETH-1 with the parameters unchanged. BTC C2 (2026), ETH-2 (2022–2025) and ETH 2026
are **not** loaded.

## Data

- **Design: BTC 2018-04-01 → 2026-01-01.** Prices from Binance spot 1h before 2020-01-01 (perp did not
  exist yet) and from Binance USDⓈ-M perp 1h after. Funding from the real perp settlements from
  2020-01; set to 0 before 2020 (a short usually receives funding, so this is slightly conservative).
  Warm-up from spot 2017-08-17.
- **Validation, once: ETH-1 2018-04-01 → 2022-01-01**, same splice (ETH spot 1h before 2020-01, ETH perp
  after, ETH funding from 2020-01). Only if a BTC rule passes.

Daily bars are built from the 1h bars (UTC days). Signals are decided at the day close and filled at
the next day's first 1h open.

## Rule families (33 variants)

ATR = 14-day Wilder ATR on daily bars. Every trade has a stop loss resting on the exchange at
entry + k × ATR, simulated intrabar on 1h high (filled at the level, or at the open on a gap), and
take profit (where used) at entry − 2 × ATR on 1h low. If one 1h bar touches both, the SL fills first.

- **S1 band short (mirror of the R10a band).** Short state turns on when close < SMA(n) × 0.98 and off
  when close > SMA(n) × 1.02. Enter on the day it turns on. After a stop-out, re-enter only on a new
  10-day closing low while the state is still on. Cover when the state turns off.
  n ∈ {50, 100, 200}, k ∈ {2, 3, 4} → 9.
- **S2 Donchian breakdown.** Enter when close < lowest close of the previous N days; cover when
  close > highest close of the previous M days. N ∈ {20, 55}, M ∈ {10, 20}, k ∈ {2, 3}, filter
  ∈ {none, close < SMA200 at entry} → 16.
- **S3 pump fade.** Enter when the 5-day return > p. TP 2 × ATR, time stop 5 days.
  p ∈ {15%, 25%}, k ∈ {1.5, 2.5} → 4.
- **S4 crowded-long fade.** Enter when the mean funding of the last 3 days (9 settlements) > f per 8h and
  close < EMA10. TP 2 × ATR, time stop 5 days. f ∈ {0.03%, 0.05%}, k ∈ {1.5, 2.5} → 4.
  (No trades before 2020: no funding data.)

## Trade management: daily (D) vs hourly (H)

Note added (2026-11-18, before any run) that holding a losing short until the next daily close may be
the problem, so an open short should be checked every hour. Every variant above runs in both modes:

- **D (daily):** exits only by the exchange SL/TP and the daily signal / time stop, as above.
- **H (hourly):** same entries and the same SL/TP/daily exits, plus, at every 1h close while short:
  (a) once the trade has been ≥ 1 × ATR in profit (1h low ≤ entry − ATR), the SL moves to the entry
  price; (b) if the 1h close is above the highest 1h close of the previous 24 hours, the short is closed
  at that close.

So 33 × 2 = 66 variants. Selection below treats each (family, mode) pair as its own family for rule 5.

## Sizing (Note's 1x vs 2x) and risk rules

- **R1 (standard rule):** notional = 1% of equity ÷ SL distance, so a stop loses about 1% of equity;
  capped at 2× equity (the number of trades where the cap binds is reported).
- **N1:** notional = 1× equity. **N2:** notional = 2× equity. Same SL/TP.
- In every mode: **daily cap** = if equity is 3% below its value at the start of the UTC day, the
  position is closed at that hour's close and no new entry is made that day; **total cap (HALT)** = if
  equity falls 10% below its peak, the position is closed and the strategy stops for the rest of the
  window. N1/N2 are also reported without HALT (diagnostic only), so the size of the drawdown is visible.
- Liquidation is checked every hour on the 1h high (cross margin on the whole account, maintenance 0.5%).
- Costs: 0.05% fee + 2 bps slippage per side; ×2 cost stress. Shorts receive positive funding and pay
  negative funding at the real settlement hours. Start equity 10,000 USDT.

## Selection (BTC, R1 sizing) — a variant passes only if all hold

1. Net > 0 at base costs and at 2× costs.
2. Max DD ≤ 10% and HALT never triggered.
3. Sharpe (daily, ×√365) ≥ 0.5.
4. No calendar year worse than −5%.
5. Robustness: at least half of the variants in its family have net > 0 (R1, base costs).

The passer with the highest Sharpe is the single R23 rule. If none pass, R23 fails and ETH-1 is not used.

## ETH-1 (once, parameters unchanged)

The chosen rule in R1, N1 and N2. Pass = R1 net > 0 and max DD ≤ 10% with no HALT. N1 vs N2 is
reported for the 1x vs 2x comparison. Reported everywhere: net, per-year, Sharpe, max DD, trades, win
rate, exits by SL / TP / signal / time / cap, time in market, fees, funding, cap triggers.

## BTC result (run once 2026-11-18; `/mnt/project-files/r23-results/btc/`)

2018-04 → 2025, 66 variants. **Under the 1% risk rule every variant is close to zero**: best net +8.3% over
7.75 years (S1 SMA100 k2 daily, Sharpe 0.26, DD 7.6%); notional per trade only ~0.03–0.10× equity because
ATR stops are 10–30% away. S3 pump fade with p = 15% loses (−7 to −10%, hits the 10% HALT); S4 crowded-long
fade is small and mixed.

**Only one variant passes all five criteria: S3_p25_k2.5_D** (short after a > 25% 5-day pump, daily
management): +2.9% total, Sharpe 0.55, DD 0.7%, **6 trades in 7.75 years** (2018–2021 only, none since).
That is far too few trades to show an edge; it passes on paper only.

**1x vs 2x full notional:** with the 3% daily cap and 10% HALT on, 51 of 66 (1x) and 59 of 66 (2x)
end ≤ 0; nearly all of the rest made their gain in the 2018 bear and then hit HALT and stopped for good
(e.g. S2 N55 1x: +28% in 2018, HALT 2019-09). Only S3 p25 hourly at 1x (+7%, DD 8.5%, 8 trades) never halted. Without HALT (diagnostic), most are negative with 30–90% drawdowns; 2x is worse than 1x
in 60 of 66 variants because the 3% daily cap is hit twice as often (it acts as a tight stop). **Hourly
management (H)** cuts DD under R1 (e.g. S1 SMA100: 7.6% → 3.5%) but also cuts profit; it does not create
an edge. Per plan, ETH-1 is run once with S3_p25_k2.5_D.
