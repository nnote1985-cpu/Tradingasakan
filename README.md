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

## Phase 1 Foundation: local commands

```bash
uv sync                      # Python 3.13, locked dependencies
uv run ruff check .          # lint
uv run mypy                  # strict types on Foundation modules
uv run pytest -q             # unit tests + migrations on a throwaway local PostgreSQL (needs PG server binaries; set PG_BIN if not under /usr/lib/postgresql)

# API shell (needs SUPABASE_URL and DATABASE_URL in the environment; never commit them)
uv run uvicorn --factory api.main:app_from_env --app-dir apps/api --port 8000
```

Mode defaults to SHADOW. `TRADING_MODE=LIVE` is refused unless `LIVE_ENABLED=true` with `APP_ENV=production`.
Operators are allowlisted in `trading.operator_roles`; roles in the JWT are ignored.
