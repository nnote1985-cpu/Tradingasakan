# tn-r10a-code-v1 soak replay fixture (M3 plan §4.1)

- `decisions.json`: every `trading.exec_strategy_decisions` row of book `tn-r10a-code-v1`, account `DEFAULT`
  (18 rows, 2026-10-18 … 2026-11-13), exported from Supabase on 2026-11-15.
- `klines_1d.csv`: BTCUSDT perp daily klines from data.binance.vision (2025-07-01 … 2026-11-13), downloaded 2026-11-15.
  The archive had no 2026-10-28 until 2026-11-11, so the replay drops that row for earlier days.
- `funding_1d.csv`: daily funding from data.binance.vision as of 2026-11-15 (2026-09-01 … 2026-11-13).
  The replay takes the 7 days inside each stored snapshot as the worker saw them (print or proxy) and only
  the days before that window from this file; without that history pandas' rolling mean differs in the last bit.

Rebuild with `uv run python scripts/replay_r10a_v2.py --decisions <export.json> --download`.
