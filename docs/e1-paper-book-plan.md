# Forward paper book for R13 E1 Donchian_Breakout (spot-e1): plan and acceptance, declared before the code

Written 2026-11-12, before any book code. R13 (`docs/research/r13-open-source-ea-screen-plan.md`) found
one finalist on dev data, E1 Donchian_Breakout (Argus, MIT). On 2026-11-12 Note chose "รัน paper ก่อน"
on the card "จะเอา E1 Donchian ไปทดสอบกับ C2 เลยไหม": **C2 stays unspent** and E1 runs forward as a
paper book. Code only, simulated money, no exchange orders, no AI calls.

## Rules (the R13 E1 port, unchanged; no new parameter)

- Signals: BTCUSDT USD-M perp 1h bars from data.binance.vision, grouped into UTC H4 bars, exactly as
  in R13: close1 > highest high of H4 bars 2–21, close1 > EMA50 and EMA200, ADX14 ≥ 20 and rising.
  Long only (spot); short signals are ignored.
- Stop: lowest low of H4 bars 1–20 minus 0.02, trailed up to that level at each new H4 bar; the
  first trailing move drops the 2.5R target (as the EA's code does).
- Size: 1% of equity at risk from entry to stop, notional capped at 1× equity.
- **Fills and marks on BTCUSDT spot** (the market a spot book would trade), like the other forward
  spot books: entries at the spot open of the next H4 bar, stops/targets checked on spot 1m bars,
  equity marked on spot 1h closes. Fee 0.10% + 2 bps, stops 5 bps. This is the one difference from
  R13, which used perp prices for fills (perp/spot basis averaged −0.05% in 2025–2026, see
  results-register).
- Risk: `spot.v1` levels on hourly marks: alert at a 5% day loss or 15% drawdown; at 25% drawdown
  the position is sold at the next hour's open and the book stops (HALT). A HALTed book is not
  resumed; any restart is a new version.

## How a run works

The book is a pure replay. Each daily run (after the day's archive files are published):

1. Fetches perp 1h from `start − 120 days` (indicator warm-up) and spot 1h and 1m from `start`, up to
   yesterday. Only whole UTC days with 24 hourly and 1,440 one-minute rows are used; the replay stops
   before the first incomplete day and the run says so.
2. Replays the frozen rules from `start` through the last complete day.
3. Checks that every trade already recorded is reproduced exactly (same times, prices, quantities).
   A mismatch is a problem: nothing is written and the run says so.
4. Appends the new entries (trade opened, stop moved, trade closed, alert, HALT), writes `state.json`,
   `journal.md` (Thai), and prints the JSON summary and Thai text, like the other books.

The first run only writes `start` = the first UTC day after the last complete day it saw. Warm-up
data before `start` is read for indicators only; no trade or equity before `start` is computed, so
C2 results are never produced.

State lives under `/mnt/project-files/paper-trading/spot-e1-v1/`. The daily task, `paper_run.py`
wiring and the trading.* sync belong to the thread that owns the daily job; this PR does not touch
them. After merge, branch `frozen/spot-e1-v1` is created and the wiring is handed over.

## Acceptance tests

1. **Same rule as the research.** With fills on the same series as the signals, the book's replay
   gives the same trades and daily equity as `ea_ports.simulate(E1Donchian, market="spot",
   sizing="native")` (synthetic bars in CI; also checked once on the 2022–2025 dev data).
2. **Warm-up does not matter.** On dev data, replays with 120 and 365 days of warm-up give the same
   trades from a start date (checked once on real data; not in CI).
3. **Replay is stable.** Replaying through day N, then through day N+1, gives the same trades up to
   day N; a changed recorded trade is reported as a mismatch.
4. **HALT.** On synthetic bars that fall 30%, the book sells after the 25% drawdown mark and makes no
   later trade.
5. Existing tests still pass; ruff clean.

## Not in this PR

No forward run, no daily-task change, no Supabase writes, nothing live.

## Results (added after the code; no rule above was changed)

- `trading.strategies.argus_e1` (rule + `replay`), `scripts/paper_trade_e1.py`, `tests/unit/test_e1_book.py`.
- Tests, 7 pass: on synthetic minute data (3 seeds) the replay gives the same closed trades and daily
  equity as `ea_ports.simulate(E1Donchian, spot, native)`; replay through day N+1 keeps day N's trades;
  120 vs 240 days of warm-up give the same trades; a −30% gap through the stop HALTs the book and no
  trade follows; `complete_through` stops at the first incomplete day.
- Checked once on the 2022–2025 dev data (not in CI): all 65 R13 trades identical and daily equity
  equal to the last bit; with HALT on, the book never HALTs on dev (max DD 4.4%); 120 vs 365 days of
  warm-up give identical trades from 2023-01-01, 2024-03-01 and 2025-02-01.
- Smoke runs against live data with throwaway state (2026-11-12 01:30 UTC): a first run writes start =
  2026-11-12; a state with start 2026-11-02 replays to 2026-11-10 (11-11 not yet published), no trade
  (BTC −5.9% over those days, no breakout), and a second run reproduces it with no problem.
- Full suite 679 passed; ruff and mypy clean.

## Daily job (added after merge, by the thread that owns the daily job)

The book runs once a day in the daily task, from its frozen worktree, wrapped by main's
`scripts/e1_run_record.py`: before the run it notes the sha256 of `state.json`; after it, it saves
the run's JSON summary as `spot-e1-v1/daily/<data_through>.json` (skipped when the run has no
`data_through` or reports problems) and prints the `trading.audit_events` insert (before and after
hash). `scripts/trading_sync_sql.py` mirrors the book as `spot-e1-v1/paper` and
`spot-e1-v1/buy_and_hold`: fills are rebuilt from the replayed trades with the book's own cost rule
and must sum to the book's cash; the saved summaries become the daily equity snapshots (the state
keeps no daily marks). `scripts/supabase_sync_sql.py` skips the book. Neither touches `state.json`.
