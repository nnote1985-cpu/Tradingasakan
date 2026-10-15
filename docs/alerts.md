# BTC risk alerts (path A)

Owner decision 2026-10-15: hold BTC as the core; the system **only alerts**
and suggests an allocation. A person decides and places any order. No exchange
keys exist in this repository.

## What it checks (daily, after the UTC day closes)

| Signal | Rule | Source |
| --- | --- | --- |
| Trend | Close vs 100-day SMA | v2 research (`docs/research/v2-plan.md`) |
| Funding | 7-day mean > 0.03% per 8h | v3 research (`docs/research/v3-funding-overlay-plan.md`) |
| Volatility | 30-day realised > 80%/year | context only |
| Drawdown | > 20% below the 1-year high | context only |
| Open interest | 7-day change | context only |

Suggested BTC weight = 100% × (0 if below SMA100) × (0.5 if funding hot).
These rules lost to buy & hold in 2024 (a strong bull year); they trade some
upside for a smaller worst case. Treat the weight as a prompt to think, not
an instruction.

## Run

```bash
SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python scripts/risk_check.py
```

Data comes only from data.binance.vision (the Binance REST API refuses this
environment with HTTP 451). A closed UTC day appears there about 8 hours
after midnight UTC. Current-month funding is estimated from premium-index
klines (error ~0.002% per print, checked on 2025-09).
