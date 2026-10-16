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

## Result (run once, 2026-10-16, `research/spike/run_r9.py`; outputs in `/mnt/project-files/r9-results/`)

Data: 1,280 days in the window, no missing price, funding or vol days; the one-year median was not
ready on the first 28 days (R9b neutral there, as declared). Vol was above 0.80 on 49 days, only 9 of
them while path A held BTC. R9b scaled the weight on 309 days (0.75 on 432 days and 0.5 on 94 days of
the whole window, any base weight).

2022-07-01 → 2026-01-01, 10,000 USDT, fee 0.10% + 2 bps:

| | Net | Sharpe | Max DD | Trades |
| --- | --- | --- | --- | --- |
| path A (base) | +176.5% | 0.843 | 35.1% | 82 |
| R9a cap | +172.3% | 0.836 | 35.1% | 92 |
| R9b target | +142.1% | 0.832 | 26.8% | 362 |
| BTC buy & hold | +339.2% | 0.880 | 35.4% | 1 |

By year (Sharpe, 2022 Jul–Dec / 2023 / 2024 / 2025): path A −1.77 / 1.48 / 1.23 / 0.17; R9a −1.77 /
1.48 / 1.21 / 0.17; R9b −1.86 / 1.25 / 1.47 / 0.13. With costs ×2 the order is the same (path A 0.79,
R9a 0.78, R9b 0.77; R9b max DD 28.0% vs 36.4%).

**Verdict: both fail** criterion 1, since neither Sharpe is above path A's. R9a almost never acted,
because path A is usually already out of BTC when vol is above 80%. R9b is the closer miss: it cut
max drawdown by 8.3 points (35.1% → 26.8%) at nearly the same Sharpe (0.832 vs 0.843), paid for with
lower return and 4× the trades. Neither meets Note's objective against buy & hold. As declared, no
other thresholds, windows or targets are tried on this data, and C2 stays unspent.
