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

## Results (run 2026-10-15, after this plan was committed)

`uv run python research/spike/run_v3.py` → `reports/v3/` (not committed).

| BTC book | Development net | Sharpe | Max DD | Validation net | Sharpe | Max DD |
| --- | --- | --- | --- | --- | --- | --- |
| **v3 funding overlay** | **+28.0%** | **0.25** | **62.0%** | **+109.5%** | **1.58** | **24.8%** |
| BTC buy & hold | +31.0% | 0.27 | 62.0% | +102.0% | 1.43 | 26.3% |

- Funding was elevated on 0.9% of development days and 5.6% of validation
  days. The 2022 crash came with low funding, so the overlay never reduced
  exposure before it.
- Diagnostics (nothing selected): thresholds 0.06%/0.12% per day and weights
  0%/75% give the same picture: no change to the 62% development drawdown,
  validation Sharpe 1.42–1.67.

### Verdict

Validation passes (Sharpe 1.58 > 1.43). Development fails (Sharpe 0.25 < 0.27,
drawdown unchanged). **v3 does not pass.** Holdout untouched.

### Post-hoc check (in-sample, not a test)

After seeing v2 and v3, the obvious combination (BTC only, SMA100 trend state
× funding overlay) was computed on the same data. It is reported only to show
whether it is worth spending the holdout on:

| Combination (in-sample) | Net | Sharpe | Max DD |
| --- | --- | --- | --- |
| Development | +85.2% | 1.00 | 26.8% |
| Validation | +55.7% | 1.15 | 35.1% |

It still loses to BTC buy & hold in validation (Sharpe 1.43, DD 26.3%), so
the holdout is not spent on it.

### What this says

The two signals protect in different regimes: the trend state cut the 2022
bear drawdown from 62% to 27%; the funding overlay trimmed risk in the
overheated 2024 bull. Neither, nor both together, beat holding BTC through a
strong bull year. Under path A they are useful as **alerts with a stated
cost** (lower return in strong bulls in exchange for a smaller worst case),
not as a system that beats the market on these years.
