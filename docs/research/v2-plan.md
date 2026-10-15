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

## Results (run 2026-10-15, after this plan was committed)

`uv run python research/spike/run_v2.py` → `reports/v2/` (not committed).
Portfolio = BTC + ETH + SOL books, 30,000 USDT, spot costs (0.10% + 2 bps).

| Portfolio | Development net | Sharpe | Max DD | Validation net | Sharpe | Max DD |
| --- | --- | --- | --- | --- | --- | --- |
| **v2 (SMA100)** | **+47.6%** | **0.56** | **34.7%** | **+8.1%** | **0.18** | **45.5%** |
| Buy & hold (equal weight) | +13.9% | 0.11 | 72.0% | +80.2% | 1.02 | 34.2% |

Per coin (v2 / buy & hold):

| Coin | Dev net | Dev Sharpe | Val net | Val Sharpe | Val DD |
| --- | --- | --- | --- | --- | --- |
| BTC | +89.6% / +31.0% | 1.03 / 0.27 | +50.1% / +102.0% | 0.98 / 1.43 | 36.4% / 26.3% |
| ETH | +8.3% / -1.2% | 0.10 / -0.01 | -21.8% / +12.2% | -0.55 / 0.19 | 49.2% / 45.4% |
| SOL | +44.7% / +12.0% | 0.32 / 0.05 | -4.1% / +126.5% | -0.07 / 1.04 | 60.2% / 38.4% |

- Validation SOL result checked with an independent vectorised calculation
  (gross ~0%, max DD 59%, 34 switches): the choppy 2024 range around the SMA
  caused repeated buy-high/sell-low switches.
- Costs matter little: validation net ranges +3.2% to +10.0% across the grid.
- Perpetual with funding: development +43.7%, validation +7.9%.
- Diagnostics (nothing selected): SMA50 dev +149.8% / val +32.3% (Sharpe 0.69);
  SMA150 (from 2022-06-01) dev +114.8% / val +39.4% (Sharpe 0.72). No variant
  beats buy & hold's validation Sharpe of 1.02.

### Verdict against the pre-declared criteria

Development passes all three conditions. Validation fails: net is positive
(+8.1%) but Sharpe 0.18 < 1.02 and max drawdown 45.5% > half of 34.2%.
**v2 does not pass.** Holdout untouched.

The pattern matches the known behaviour of trend filters: they win in bear and
recovery markets (2022–2023) and lose to buy & hold in a steady bull market
with sideways chop (2024). The validation window is one strong bull year.
