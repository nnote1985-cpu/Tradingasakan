# Phase 1 plan (rescoped to path A, runs alongside the forward test)

Written 2026-10-15. **Correction:** buildplan v4.2 §24 defines Phase 1 as
Foundation (pinned monorepo/config/CI, single-workspace auth, minimum ledger
schema, API shell, contracts), which is mostly strategy-agnostic; the v1-bound
work is Phase 3 and 5. This file is a proposed alternative that front-loads
path A tooling; see docs/adr/0001-path-a-after-phase-0.5.md for the proposal
to keep the buildplan's Phase 1 instead.

## Rules for running development and testing together

1. **The book under test is frozen.** `RULES_VERSION` in
   `backend/src/trading/paper/ledger.py` names the behaviour; the commit that
   introduced it is pushed as branch `frozen/<version>` and never changes.
   The daily task runs the book from a worktree of that branch.
2. **Any behaviour change gets a new version.** Signals, thresholds, data
   handling, fill timing or costs: bump `RULES_VERSION`, push a new
   `frozen/<version>` branch, start a new book in
   `/mnt/project-files/paper-trading/<version>/`. Old books keep running, so
   results stay separate and comparable on the same days.
3. **Refactors that do not change behaviour** stay on the development branch
   and do not need a version; a state file stamped with another version is
   refused by `scripts/paper_trade.py`.

## How long until the result means something

`research/spike/run_pathA_power.py` (dev + validation only, holdout untouched;
report in `/mnt/project-files/pathA-power/report.json`):

- pathA-v1 changed weight about 15 times a year (65 trades in 2.8 years) and
  sat fully out of BTC on 43% of days.
- Owner's pass rule (net > 0 and Sharpe above buy & hold, or max drawdown at
  most half), 90-day block bootstrap, assuming 2022–2024 behaviour is the truth:

| Forward window | Pass if history repeats | Pass by a no-skill rule (same exposure, random timing) |
| --- | --- | --- |
| 90 days | 14% | 32% |
| 180 days | 31% | 31% |
| 1 year | 46% | 29% |
| 2 years | 60% | 27% |
| 3 years | 68% | 24% |
| 5 years | 74% | 21% |
| 10 years | 87% | 14% |

The forward test cannot confirm performance on any practical horizon: the edge
comes from a few long bear regimes, not from many independent signals. Its
job is to prove the system runs correctly (timing, data, fills). The only
clean performance test available now is the untouched holdout
(2025-01-25 → 2025-12-31), which can be spent once on a frozen version.

## Build steps (in order)

1. Versioned books and frozen runner (done: `pathA-v1`).
2. Storage: move ledger, decisions, fills and daily equity from JSON into
   Supabase Postgres (needs a Supabase project chosen by the owner).
3. Notifications: Telegram bot for trades, data problems and weekly status
   (needs a bot token from the owner).
4. Dashboard: one page showing every book against buy & hold.
5. Candidate versions as shadow books, each pre-declared in docs/research
   before it starts.
