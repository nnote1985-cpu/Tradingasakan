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

## Paper trading (forward, simulated money)

`scripts/paper_trade.py` runs a 10,000 USDT simulated book that holds
`suggested_btc_weight` of BTC, next to a BTC buy & hold book with the same
costs (0.10% fee + 2 bps). No exchange orders are sent.

Timing follows when the data actually arrived. The archive publishes a closed
UTC day about 8 hours later, so each decision is stamped with the time the run
received the data (`received_at`) and filled at the open of the first 1h bar
starting at or after that time. That bar's price is in the archive a day
later, so a decision placed on one run is filled on the next. Buy & hold fills
at the same moment as the first order.

Data checks: if the latest close has no 100-day price history or no complete
7-day funding, the suggestion is "not enough data" and the book keeps what it
holds (journal kind `no_decision`). If a closed day is missing while later
days exist, or the fill hour is missing, the book stops and reports it rather
than skipping ahead. Loaders trim monthly files to the requested dates.

State and journal: `/mnt/project-files/paper-trading/{state.json,journal.md}`.
Opened on the 2026-10-13 close at 100% BTC, received 2026-10-15 06:39 UTC,
first fill at the 2026-10-15 07:00 UTC open.
