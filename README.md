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

### Start the web and API together

Set up dependencies once with `uv sync` and `pnpm --dir apps/web install --frozen-lockfile`.
Copy `.env.example` to `.env` at the repository root and fill in the backend values.
Configure frontend values in `apps/web/.env.local`.

From the repository root:

```bash
npm run dev
```

This starts the API at `http://localhost:8000` with Python code reload and the web
at `http://localhost:5173` with Vite hot reload. Press `Ctrl+C` to stop both.
Restart the command after changing the backend `.env` file.
For `npm run dev`, root `.env` values override inherited terminal variables so
stale exported credentials do not mask changes to the local file.
The launcher uses Node's built-in APIs; no root dependency install is needed.

### Research commands

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

# API shell (copy .env.example to .env at the repository root and fill in your values)
uv run uvicorn --factory api.main:app_from_env --app-dir apps/api --port 8000
```

The API automatically reads the repository root `.env` file as UTF-8, regardless of
the working directory. Process environment variables override `.env` values.
Keep secrets in `.env` (gitignored); never commit them. Frontend settings remain
in `apps/web/.env.local` using the `VITE_` variables defined in `apps/web/src/config.ts`.

Mode defaults to SHADOW. LIVE is unavailable in this build, even with
`LIVE_ENABLED=true` and `APP_ENV=production`.
Operators are allowlisted in `trading.operator_roles`; roles in the JWT are ignored.
