# R9: realised volatility as a filter on path A (pre-declared)

Written 2026-10-16, before any R9 backtest was run. After R8 failed, Note agreed ("โอเค") to test
volatility next, on existing data, without spending C1/C2. Exploratory: it can say whether this is
worth a confirmation run, not prove an edge.

## Question

Does holding less BTC when recent volatility is high improve path A's risk-adjusted result?

Hypothesis, declared before running: BTC's large falls tend to come in high-volatility stretches,
and volatility persists from one month to the next, so scaling exposure down when trailing
volatility is high should cut drawdown more than return (the "volatility targeting" idea).

## Data

The 1h klines and funding already used for path A (Binance USD-M BTCUSDT, data.binance.vision,
checksums verified), 2022-01-01 → 2025-12-31. No new data. C1 (2020-04 → 2022-01) and C2
(2026-01 → 2026-10) are not used.

## Feature (known at the close of UTC day d)

- `vol_d` = `vol_30d` from `trading.alerts.risk_check.signals`, unchanged: standard deviation of
  the last 30 daily log close-to-close returns × √365. A gap inside the 30 days gives NaN.
- `med_d` = median of `vol_30d` over days d−364 … d, needing at least 180 valid days, else NaN.

## Rules (two trials, nothing else)

Base weight `w_d` = path A rule, unchanged: (close > SMA100) × (0.5 if 7-day mean funding
> 0.09%/day else 1), from `signals(...)["suggested_btc_weight"]`.

- **R9a cap:** if vol_d > 0.80 then w_d × 0.5. The 0.80 is the existing `HIGH_VOL` alert level in
  `risk_check.py`, set before this test; it is not chosen here.
- **R9b target:** w_d × s_d, where s_d = min(1, med_d / vol_d) rounded **down** to a multiple of
  0.25, with a floor of 0.25. The target is the trailing one-year median, so no level is chosen.

If vol_d (or med_d for R9b) is NaN the filter is neutral that day (×1). Decided at day d's close,
filled at day d+1's open (`funding_overlay_v3.simulate_weights`), BTC spot model with perp prices
as proxy, 10,000 USDT, fee 0.10% + 2 bps per trade, as in R8 and the path A holdout test.

## Window

Evaluation 2022-07-01 → 2026-01-01 (from 2022-07 so the one-year median has its 180 days; the
SMA100 and funding inputs are ready earlier). Also reported per calendar year (2022 from July,
2023, 2024, 2025). This overlaps the data path A was built and tested on, so it is a screen, not
a clean out-of-sample test.

## Pass criteria (per variant, on the full window), same as R8

1. **Better than path A:** Sharpe(R9x) > Sharpe(path A) **and** max DD(R9x) ≤ max DD(path A)
   **and** net(R9x) > 0.
2. Reported against Note's objective as well (net > 0 and Sharpe > B&H or max DD ≤ ½ B&H), but
   the decision to go further rests on criterion 1.

Also reported: days the filter cut the weight, trades, the result with costs ×2. A variant that
passes is a candidate for a confirmation run on C2 (Note's separate approval) and, after the
Phase 5 day-28 review, a forward paper book. A variant that fails is recorded and not retuned (no
other thresholds, windows or targets are tried on this data).
