# Tradingasakan

Research-first crypto trading agent. This repository currently holds the
**Phase 0.5 research spike** from BUILDPLAN v4.2 §4.3: a standalone backtest of
`trend_breakout_v1.0` on BTCUSDT perpetual data, with cost/parameter sensitivity
and a power simulation for the later AI-filter experiment.

Live trading is not part of this repository yet. There are no exchange keys here.

## Layout

| Path | What it is |
| --- | --- |
| `backend/src/trading/features` | EMA, Wilder ATR, breakout levels |
| `backend/src/trading/strategies` | `trend_breakout_v1` signal (§6) |
| `backend/src/trading/risk` | Decimal position sizing and rounding (§9) |
| `backend/src/trading/simulator` | BAR_RESEARCH backtester (§10) |
| `backend/src/trading/evaluation` | Metrics, block bootstrap, power simulation (§17) |
| `backend/src/trading/market_data` | Binance public data downloader with checksum checks |
| `research/spike` | Scripts that produce the Phase 0.5 report |
| `configs/spike.json` | Pre-declared data range, partitions and sensitivity grids |
| `docs/research/phase-0.5-spike.md` | Method, assumptions and results |

## Run

```bash
uv sync
uv run pytest
uv run python research/spike/download_data.py      # needs access to data.binance.vision
uv run python research/spike/run_backtest.py       # writes reports/spike/
uv run python research/spike/run_power.py
```
