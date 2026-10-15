# Phase 0.5 research spike: trend_breakout_v1.0

Status: **code ready, results pending real data.** No backtest numbers are
reported here until they come from real market data.

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
- Status: download blocked from the cloud environment (network policy). Needs
  `data.binance.vision` added to the allowed domains, or the files uploaded.

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

Pending real data.
