# Supabase schema: how migrations are applied

Project: asakantrading (`bdpgilvgefnhzswjrmdx`). Schemas `paper` (interim mirror) and
`trading` (Phase 1 Foundation) are private: RLS on, no policies, only `service_role`
has grants. See docs/source-of-truth.md for which data wins.

## Files

| File | What it is |
| --- | --- |
| `migrations/<version>_<name>.sql` | One migration each. The version is the one Supabase recorded when it was applied. |
| `migration_history.txt` | Versions recorded remotely, in order (`supabase_migrations.schema_migrations`). |
| `schema_fingerprint.txt` | Object count and md5 of the live `paper`/`trading` schema, from `tests/schema_fingerprint.sql`. |
| `tests/bootstrap_local.sql` | Stand-ins for Supabase roles/`auth` so the migrations run on plain Postgres in CI. |

`tests/integration/test_migrations.py` checks that the migration files match
`migration_history.txt` and that a fresh database built from these files has the same
fingerprint as the live one. A new database is built from repo files only.

## Adding a migration

1. Write the SQL and run the integration tests locally.
2. Check what is already applied before touching the live database: list migrations
   (Supabase connector `list_migrations`, or
   `select version, name from supabase_migrations.schema_migrations order by version`)
   and compare with `migration_history.txt`. If they differ, stop and find out why.
3. Apply once with `apply_migration`. Never re-run a migration that is already in the
   history; a change is always a new migration.
4. Rename the file to the version Supabase recorded, append it to `migration_history.txt`,
   run `tests/schema_fingerprint.sql` on the live database and write the result to
   `schema_fingerprint.txt`. The parity test must pass in the same PR.

No step here enables live trading or adds credentials; secrets stay in environment settings.
