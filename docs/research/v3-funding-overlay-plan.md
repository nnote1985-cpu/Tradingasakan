# v3 plan: BTC core with a funding-rate risk overlay (pre-declared)

Written 2026-10-15, **before any v3 backtest was run and before looking at the
funding-rate distribution**. Owner chose path A: hold BTC as the core and use
non-price data to reduce risk; the AI's role is to watch and alert.

## Hypothesis

When perpetual funding stays high, longs are crowded and leveraged; such
periods are more often followed by sharp drawdowns. Cutting BTC exposure while
funding is elevated should lower drawdown and raise risk-adjusted return
versus holding BTC throughout.

## Rules (fixed now)

- One book, 10,000 USDT, BTC only (spot costs: 0.10% fee + 2 bps per trade).
- Funding signal: sum of BTCUSDT perpetual funding prints per UTC day; take
  the trailing 7-day mean of those daily sums.
- Elevated if that mean > 0.09% per day (= 0.03% per 8h, three times the
  0.01% baseline rate). Threshold chosen from the exchange baseline, not data.
- Exposure: 50% BTC / 50% USDT while elevated, otherwise 100% BTC. Decided at
  the daily close, traded at the next day's open; only the difference is traded.
- No price-based rules, no leverage, no shorting.
- Benchmark: BTC buy & hold on the same days with the same costs.

## Runs

1. v3 vs BTC buy & hold, development and validation (same partitions as before).
2. Diagnostics only (nothing selected): thresholds 0.06% and 0.12% per day;
   exposure while elevated 0% and 75%; share of days elevated.

## Success criteria (owner's risk-adjusted definition, fixed now)

On **both** development and validation, at default costs:

1. Net return > 0, and
2. Sharpe (daily, 365) > BTC buy & hold Sharpe, **or** max drawdown (daily
   close) ≤ half of buy & hold's.

A weaker but still useful outcome is reported separately: Sharpe higher and
drawdown lower than buy & hold on both partitions, even if neither margin is large.

The holdout (2025-01-25 to 2025-12-31) stays untouched.
