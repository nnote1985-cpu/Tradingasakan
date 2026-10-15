# ADR 0001: Continue with path A after Phase 0.5; keep buildplan Phase 1 Foundation

Status: accepted 2026-10-15 (owner chose to follow buildplan Phase 1).

## Context

BUILDPLAN v4.2 §24 says that when Phase 0.5 shows few signals, missing data or
high costs, the hypothesis or scope changes through an ADR and the trial
registry. Phase 0.5 results (docs/research/):

- v1 trend breakout, v1.1, v2 SMA100 regime and v3 funding overlay all failed
  their pre-declared criteria; power simulation for v1 → REVISE_HYPOTHESIS.
- The owner chose path A: BTC core, risk overlay, simulated trading first.
- pathA-v1 (SMA100 × funding) was frozen and spent the untouched holdout:
  net -6.9% vs buy & hold -16.5%, fails the owner's rule (net must be > 0).
- Power: a forward test cannot confirm performance on any practical horizon
  (46% power at 1 year, 29% false pass), see docs/phase-1-plan.md.

## Decision

1. Phase 1 stays as defined in §24 (Foundation): pinned monorepo/config/CI,
   single-workspace auth, minimum ledger schema per §20 in the private
   `trading` schema via Supabase CLI migrations, API shell. Analysis
   contracts (§7) wait until an AI hypothesis exists.
2. The strategy under test changes from `trend_breakout_v1` to path A
   versions registered as `strategy_versions` (pathA-v1 = commit 42920ba,
   branch frozen/pathA-v1).
3. Work already done ahead of order (Phase 2–3 pieces for path A: receipt-time
   stamps, checksums, gap checks, paper ledger, fill timing) is kept and later
   moved onto the Foundation schema.
4. The interim `paper` schema in Supabase (applied directly through the
   connector, not via CLI as §20.4 requires) is a mirror only and will be
   replaced by CLI-managed migrations.
