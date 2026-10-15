# v2 plan: trend_regime_v2 (pre-declared)

Written 2026-10-15, **before any v2 backtest was run**.

## Goal definition (confirmed by owner 2026-10-15)

"Beat the market" is risk-adjusted: compared with buy & hold of the same coins
over the same period, after costs, the strategy must be profitable **and**
either have a higher Sharpe ratio or a much lower maximum drawdown.

## Why a new hypothesis

v1 and v1.1 risk 0.5% per trade, so they hold ~5% of equity on average and
cannot keep up with buy & hold's return, and neither showed a stable edge
(`phase-0.5-spike.md`, `v1.1-plan.md`). Under a risk-adjusted goal the simplest
candidate with a long research record is a trend regime filter: hold the coin
in uptrends and step aside in downtrends, giving up some upside to avoid most
of the large drawdowns. This is chosen from that prior, not from v1/v1.1 results.
Development and validation have already been looked at for other rules, so
the holdout (2025-01-25 to 2025-12-31) remains the only clean test.

## v2 rules (fixed now)

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, each its own 10,000 USDT book; the
  portfolio is the sum. No rebalancing between books.
- Daily bars on UTC days, built from 1h bars (a day with any missing hour is
  invalid and keeps the previous state).
- Signal at each daily close: IN if close > SMA100 of daily closes, else OUT.
- Trade at the next day's open (first 1m/1h open after the close): IN = 100%
  of the book in the coin, 1x, long only; OUT = 100% cash (USDT, no yield).
- No stops, no leverage, no shorting.
- Costs: modelled as spot. Taker fee 0.10% per side plus 2 bps slippage.
  A perpetual variant (0.05% fee + funding paid while long) is reported too.
- Warmup: the first 100 days; the run starts at the existing partition start
  (2022-04-12), where SMA100 is defined.

Benchmark: equal-weight buy & hold of the same three coins (one entry fee),
and each coin alone.

## Pre-declared runs

1. v2 vs buy & hold on development and validation: portfolio and per coin.
2. Cost sensitivity: fee {0.05%, 0.10%, 0.20%} × slippage {0, 2, 10} bps.
3. Diagnostics only, nothing selected: SMA {50, 150} (150 starts later
   because of warmup; reported on its own window), perpetual variant with funding.

## Success criteria (fixed now)

On **both** development and validation, at default costs, the v2 portfolio
must have:

1. Net return > 0, and
2. Sharpe (daily, 365) > equal-weight buy & hold Sharpe, **or**
   max drawdown (daily close) ≤ half of buy & hold's.

If it passes, the next step is to freeze v2 and run the holdout once.
If it fails, the result is reported as is.

## Note on the AI filter

v2 changes state rarely (a few times a year per coin), so it gives too few
decisions to measure an AI filter. If v2 passes, the AI's role has to be
re-planned with the owner (for example as a risk overlay), not bolted on.
