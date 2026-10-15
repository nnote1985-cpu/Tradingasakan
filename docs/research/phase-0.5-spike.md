# Phase 0.5 research spike: trend_breakout_v1.0

Status: **run on real data 2026-10-15. Verdict under the default decision rule: `REVISE_HYPOTHESIS`.**
The decision-rule thresholds below were proposed before the run and are still awaiting the owner's confirmation.

## Question

Does the v1 rule-based strategy produce a measurable edge after costs on
BTCUSDT, how often does it trade, and is the evaluation method able to detect
the size of improvement we hope an AI filter can deliver (BUILDPLAN v4.2 §4.3)?

v1 is the baseline that every later version must beat.

## Data

- Source: Binance public data (`data.binance.vision`), USDⓈ-M futures, BTCUSDT.
- Monthly 1h and 1m klines plus funding rate, each file checked against its
  published `.CHECKSUM`. `manifest.json` records every file and its status.
- Target range 2022-01 to 2025-12 (`configs/spike.json`). The report states the
  real coverage; missing months are listed, not filled.
- 4h bars are built from 1h bars on UTC boundaries; a 4h bar with any missing
  hour is invalid. Gaps are never forward-filled.
- Status: 144 monthly files (1h, 1m, fundingRate; 2022-01 to 2025-12) downloaded,
  all 144 `VERIFIED` against `.CHECKSUM`. 35,064 1h bars, 2,103,840 1m bars,
  4,383 funding prints. No missing execution bars, no invalid 1h bars.

## Partitions

Chronological, after a 100-day warmup (600 4h bars):

| Partition | Share | Use |
| --- | --- | --- |
| development | 50% | sensitivity grids, power simulation |
| validation | 25% | confirm development findings, default parameters only |
| holdout | 25% | untouched; the spike scripts never simulate it |

## Simulator assumptions (BAR_RESEARCH)

- Signal at the 1h close; entry at the first execution bar opening at or after it.
- Execution bars: 1m when available, otherwise 1h (reported in `report.json`).
- Stop and take profit in the same bar: conservative assumes the stop first;
  an optimistic run is reported as the other bound.
- A bar opening beyond the stop fills at the open; take profit is capped at
  the target.
- Default costs: taker fee 0.05%, entry/exit slippage 2 bps, stop slippage 5 bps.
  These are assumptions to verify against the real fee tier.
- Funding is paid on positions held at the funding timestamp.
- Risk: 0.5% of equity per trade capped at 50 USDT, 2% daily loss limit, 8%
  drawdown halt. Halt policy `absorbing` stops new entries for the rest of the
  run; `record_only` logs the breach and keeps trading, to show what the rule cost.
- Instrument rules (tick 0.1, step 0.001, min notional 100) are EXAMPLE values.

## Pre-declared runs

1. Base: development and validation × {conservative, optimistic} × {absorbing, record_only}.
2. Cost grid: fee multiplier {0.5, 1, 1.5, 2} × slippage multiplier {0, 1, 2, 4}.
3. Parameter sensitivity, one axis at a time on development: stop ATR multiple,
   take profit R, time exit, breakout lookback.
4. Power simulation on development (§17.3): NULL_RANDOM and ORACLE_SYNTHETIC
   filters, 90/180/365-day windows, paired moving-block bootstrap of daily log
   return differences, full portfolio-path replay.

All trials are written to `trials.csv`, including unprofitable ones.

## Decision rule (fixed before results)

- `INSUFFICIENT_DATA`: fewer than 100 labelled candidates in development, or no
  window length fits.
- `CONTINUE`: NULL_RANDOM passes in at most 10% of runs and ORACLE_MODERATE
  (40% loser recall, 10% winner false veto) passes in at least 80% of runs at
  some tested duration.
- `REVISE_HYPOTHESIS`: otherwise.

Per §4.3, a losing v1 after costs is not an automatic stop. It is recorded,
along with what the AI filter is expected to fix.

## Results

Run 2026-10-15 with `run_backtest.py` and `run_power.py` at the default config.
Execution bars: 1m. Raw outputs are in `reports/spike/` (not committed).

Partitions (after warmup):

| Partition | From | To |
| --- | --- | --- |
| development | 2022-04-12 | 2024-02-20 |
| validation | 2024-02-20 | 2025-01-25 |
| holdout | 2025-01-25 | 2026-01-01 (not simulated) |

### v1 vs Buy & Hold (default costs, conservative fills)

| Run | v1 net | B&H net | v1 Sharpe | B&H Sharpe | v1 max DD | B&H max DD | Trades | Win rate | Expectancy R | PF |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev, absorbing | -4.85% | +31.04% | -0.77 | 0.26 | 8.0% | 62.0% | 79 | 36.7% | -0.14 | 0.77 |
| dev, record_only | -8.26% | +31.04% | -0.69 | 0.26 | 17.1% | 62.0% | 281 | 37.0% | -0.07 | 0.88 |
| validation (both) | -1.39% | +102.74% | -0.22 | 1.41 | 5.5% | 26.3% | 148 | 42.6% | -0.01 | 0.96 |

- Conservative and optimistic ambiguity bounds give identical results: with 1m
  execution bars no stop/target ambiguity occurred.
- Development with the `absorbing` halt hit the 8% drawdown halt on 2022-09-23
  and was flat for the remaining ~17 months. The grid trials on development run
  with this halt, so they mostly cover 2022-04 to 2022-09.
- Monthly: v1 beat B&H in 10/23 development months (mostly falling months) and
  4/12 validation months (`monthly_*.csv`).
- Validation trade P&L: gross +384 USDT, fees 462, funding -61, net -139.
  The pre-cost edge is smaller than the cost of trading it.

### Cost sensitivity (net return)

| Partition | fee × \ slip × | 0 | 1 | 2 | 4 |
| --- | --- | --- | --- | --- | --- |
| development | 0.5 | -4.0% | -4.4% | -4.8% | -5.9% |
| development | 1.0 | -4.5% | -4.9% | -5.3% | -6.4% |
| development | 1.5 | -5.0% | -5.3% | -5.8% | -6.6% |
| development | 2.0 | -5.4% | -5.8% | -6.3% | -6.8% |
| validation | 0.5 | +5.3% | +1.0% | -0.4% | -5.3% |
| validation | 1.0 | +3.0% | -1.4% | -2.8% | -5.4% |
| validation | 1.5 | +0.6% | -3.7% | -5.1% | -5.5% |
| validation | 2.0 | -1.7% | -5.6% | -5.4% | -5.7% |

### Parameter sensitivity (development, one axis at a time)

All 12 variants lose (-3.3% to -5.9%). Least bad: time exit 12h (-3.3%),
breakout lookback 40 (-3.6%), take profit 3R (-3.9%). No variant was selected
or carried forward; these are diagnostics only.

### Power simulation (development, 12 windows × 5 seeds per filter)

543 labelled standalone candidates (61.5% losers, mean R -0.055).

| Duration | null_random | oracle_weak | oracle_moderate | oracle_strong | oracle_very_strong |
| --- | --- | --- | --- | --- | --- |
| 90 d | 10.0% | 30.0% | 45.0% | 73.3% | 95.0% |
| 180 d | 8.3% | 25.0% | 48.3% | 91.7% | 100% |
| 365 d | 0.0% | 13.3% | 65.0% | 100% | 100% |

Pass rate = share of runs whose paired block-bootstrap lower bound on daily log
return difference is above 0 (alpha 0.05, 7-day blocks).

- The method is not fooled by noise: NULL_RANDOM passes at most 10%.
- ORACLE_MODERATE (40% loser recall, 10% winner false veto) never reaches 80%,
  even at 365 days. ORACLE_STRONG (60% / 10%) reaches 80% from 180 days.

### Verdict

`REVISE_HYPOTHESIS` under the default rule (`power.json`): a moderately good
filter cannot be told apart from v1 within a year of this strategy's trade
flow. Per §4.3 the losing v1 is recorded, not a stop by itself.
