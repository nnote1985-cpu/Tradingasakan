# Source of truth for paper books

Decided in ADR 0001 (decision 6), 2026-10-15. Applies until a separate cutover ADR.

| Store | Role | Written by | Key that makes re-runs safe |
| --- | --- | --- | --- |
| `/mnt/project-files/paper-trading/<version>/state.json` (+ `journal.md`) | **Source of truth.** Decisions are frozen here first. | `scripts/paper_trade.py` on the frozen branch only | ledger append order; never edited or reset by hand |
| Supabase `paper.*` | Read-only mirror for queries | `scripts/supabase_sync_sql.py` | `(rules_version, seq)` for journal, unique rows for trades and daily equity |
| Supabase `trading.*` | Mirror into the Phase 1 double-entry ledger | `scripts/trading_sync_sql.py` | `source_event_key` = `<version>/<book>:funding`, `…:fill:<filled_at>`; snapshots by `(portfolio, at)` |

Rules:

- Data flows one way: JSON → `paper`, JSON → `trading`. Nothing reads the database back into
  a book, and no API or UI writes book data.
- Every mirror insert is `on conflict do nothing` on the keys above, so a repeated or partial
  daily run cannot duplicate entries (tests: `tests/integration/test_migrations.py`).
- `trading` journal entries are append-only and must balance to 0 per entry; a failed sync
  rolls back as a whole.
- **Reconcile:** if a mirror disagrees with the JSON, the JSON wins. Compare per book: trade
  count, last journal seq, last equity day and equity. A missing row is fixed by re-running the
  sync. A row the JSON does not have is never deleted by hand; it is reported, investigated and
  corrected with a reversing entry in a reviewed change.
- **Cutover** (making the database the source of truth) is a separate ADR. It needs at least:
  reconcile clean over a set period, the paper engine writing through the API with idempotency
  keys, backups, and owner approval. Setting environment variables does not count as cutover.
