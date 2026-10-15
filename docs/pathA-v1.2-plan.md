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
