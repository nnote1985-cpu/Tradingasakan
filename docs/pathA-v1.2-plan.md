# pathA-v1.2: pathA on the Shadow engine — plan and acceptance, declared before the code

Written 2026-10-15, before any pathA-v1.2 code. Spot track step 3 of
`docs/plan-spot-and-options.md`. Note chose "spot first" and the `spot.v1` loss
limits on 2026-10-15.

## What changes, and what does not

pathA-v1.2 keeps pathA-v1.1's strategy rules unchanged: the same daily weights
(0, 0.5 or 1 BTC) from `trading.alerts.risk_check`, the same funding policy,
`decided_at` stamps, and fills at the open of the first 1h spot bar starting
strictly after `decided_at`. Costs stay 0.10% fee + 2 bps slippage.

Only the book changes:

1. **Ledger.** The float `trading.paper.ledger` book is replaced by the Decimal
   `SpotLedger` journal (duplicate-safe, balanced entries, cash never negative).
2. **Loss limits `spot.v1`.** Alert at a 5% loss in one UTC day (trading or
   economic equity) or a 15% drawdown from the high-water mark. At a 25%
   drawdown the book HALTs: it sells all BTC at the open of the next 1h bar,
   ignores new decisions, and waits. RUIN always halts.
3. **Resume.** Only a resume record naming the person and a reason clears a
   HALT. Nothing in the code writes one by itself (no automatic resume). On
   resume the high-water mark restarts from the equity at that moment, or the
   old drawdown would halt the book again at once.
4. **Marks.** As in v1.1, equity is marked at each daily close, so the limits
   are checked once a day on closing equity (BAR_RESEARCH, not intraday).

pathA-v1.1 is not touched: its state, runs and daily job stay as they are. This
PR starts no forward book; running v1.2 forward next to v1.1 is Note's call
once this is merged.

## Acceptance tests

- **Same book as v1.1 while no limit trips.** With the same decisions and the
  same price bars, the v1.2 book and the v1.1 float book agree on every trade
  (time, side, quantity, price) and on daily equity within 1e-6 relative.
  Checked on random series and on real BTCUSDT spot bars for the 2025 holdout
  with the frozen pathA weights. The holdout is already spent, so this is an
  equivalence check, not a test of the strategy; no result from it changes any
  rule.
- **Fill timing.** No fill at or before `decided_at`; a missing 1h bar stops
  the book until it appears, as in v1.1.
- **Duplicates.** Replaying the same day or the same decision changes nothing.
- **Alerts.** A 5% day loss records one alert for that UTC day and the book
  keeps its position; a 15% drawdown records one alert per episode.
- **HALT.** A 25% drawdown sells all BTC at the next 1h open, later decisions
  are recorded as ignored, and the halt survives save and load and midnight.
- **Resume.** A resume record with a name and reason clears the halt; the next
  decision is followed again. A resume without a name is refused.
- **Buy & hold** benchmark is the same as in v1.1 (filled at the first fill,
  never halted).

## Not in this PR

No forward run, no change to the daily job, no Supabase writes, no order
adapter, nothing live.

## Results (added after the code; no rule above was changed)

- Equivalence on real data, `research/spike/check_pathA_v12_equivalence.py`
  (output `/mnt/project-files/pathA-v1.2-checks/equivalence.json`): 2025-01-24
  → 2025-12-31, 341 days, BTCUSDT spot 1h bars, pathA weights from
  `trading.alerts.risk_check`. Both books made the same 21 fills (20 paper +
  the buy & hold fill) at the same times and sides. The largest daily equity
  difference was 6.6e-11 relative, against a 1e-6 limit: **pass**. No HALT
  tripped. v1.2 recorded two alerts: a 15% drawdown at the 2025-08-30 close and
  a 5% day loss at the 2025-10-10 close.
- These books return −6.09%, against −16.16% for buy & hold. The recorded
  holdout result (−6.90%) came from `simulate_weights` with other fill timing.
  The figure here comes from the paper-book timing (fill at the first 1h bar
  after `decided_at`). It is not a new strategy result.
- Money amounts in `SpotLedger` are now kept to 1e-12 USDT, and a full sell
  removes exactly the booked inventory. Before this, the 28-digit Decimal
  rounding of products could leave a fill entry unbalanced by about 1e-24 and
  refused. The bar_backtest equivalence tests still pass.
- 221 backend tests pass; ruff and mypy are clean.

## Forward run (added 2026-10-16, after Note chose "Start v1.2")

- `scripts/paper_trade_v12.py` advances the forward book. It fetches the same inputs as
  `scripts/paper_trade.py` and records the same decision-input snapshot. State, journal and
  `runs.jsonl` live in `/mnt/project-files/paper-trading/pathA-v1.2/`. The first run opens
  the book from 10,000 USDT cash.
- The daily job runs it as
  `scripts/paper_run.py --book pathA-v1.2` from the `frozen/pathA-v1.2` worktree. It is
  recorded and checked the same way as v1.1: a hash-chained `runs.jsonl`, the
  `trading.audit_events` anchor and `simulator_valid`. Without `--book`, `paper_run.py`
  runs pathA-v1.1 exactly as before.
- From 2026-10-16 (PR for the web view) `scripts/trading_sync_sql.py` mirrors the book into
  the `trading` schema as `pathA-v1.2/paper` and `pathA-v1.2/buy_and_hold`, so it appears on
  the overview page. Its spot journal entries are copied line for line (no amount is
  re-derived) and daily marks become equity snapshots. The `paper.*` mirror skips v1.2.
  No migration: the existing tables hold it.
