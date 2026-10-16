# R10: hysteresis band and confirmation on path A's trend rule (pre-declared)

Written 2026-10-16, before any R10 backtest was run. A month-by-month look at 2025
(`/mnt/project-files/pathA-2025-analysis/README.md`) found path A's weak year came from the trend
signal, not the funding allocation: price crossed SMA100 19 times, giving 8 losing round trips
(Feb, Aug–Oct). Note asked about a hysteresis band and a confirmation filter and chose "เริ่มเลย"
to test both on existing data, without spending C1/C2.

The values (±2%, 3 days) are common textbook defaults named to Note before any run; they were not
chosen by looking at results. The 2025 analysis did look at 2025, so this is a screen, not an
out-of-sample test.

## Question

Does making path A's trend state "sticky" near SMA100 improve its risk-adjusted result over the
whole 2022–2025 period, not just in 2025?

Hypothesis: fewer whipsaw trades near the average save more than the later entries and exits cost.
Counter-risk, declared now: later re-entry after bottoms (April 2025) and later exit in sharp falls
(2022, November 2025) may cost more than the saved whipsaws.

## Data

The 1h klines and funding already used for path A (Binance USD-M BTCUSDT, data.binance.vision,
checksums verified), 2022-01-01 → 2025-12-31. No new data; C1 and C2 not used.

## Rules (two trials, nothing else)

Funding factor unchanged: 0.5 if 7-day mean funding > 0.09%/day else 1, from
`trading.alerts.risk_check.signals`. Weight = trend state × funding factor. Close and SMA100 are the
daily close and its 100-day mean from the same `signals(...)` output.

The trend state is a latch. On the first day with a valid SMA100 it starts as (close > SMA100).
After that, each day:

- **R10a band ±2%:** if out and close > SMA100 × 1.02 → in; if in and close < SMA100 × 0.98 → out;
  otherwise keep the previous state.
- **R10b confirm 3 days:** if out and the last 3 closes (days d−2, d−1, d) are all > their SMA100 →
  in; if in and the last 3 closes are all < their SMA100 → out; otherwise keep the previous state.

A day with missing close or SMA100 keeps the previous state but gives no weight (NaN), as path A
does. Decided at day d's close, filled at day d+1's open (`funding_overlay_v3.simulate_weights`),
BTC spot model with perp prices as proxy, 10,000 USDT, fee 0.10% + 2 bps, as in R8/R9.

## Window

Evaluation 2022-05-01 → 2026-01-01 (SMA100 is first valid in April 2022). Also reported per year
(2022 from May, 2023, 2024, 2025).

## Pass criteria (per variant, on the full window), same as R8/R9

1. **Better than path A:** Sharpe(R10x) > Sharpe(path A) **and** max DD(R10x) ≤ max DD(path A)
   **and** net(R10x) > 0.
2. Reported against Note's objective as well (net > 0 and Sharpe > B&H or max DD ≤ ½ B&H).

Also reported: trades, state changes, results per year and with costs ×2. A variant that passes is
a candidate for a confirmation run on C1 (2020-04 → 2022-01, which has the mid-2021 sideways
market) and/or C2, each needing Note's separate approval, and only then a new paper book; the
running pathA-v1.x books are not changed. A variant that fails is recorded and not retuned (no
other bands, day counts or windows are tried on this data).
