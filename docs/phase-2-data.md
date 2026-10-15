# Phase 2 (Data): scope and acceptance, declared before the code

Written 2026-10-15, before any Phase 2 code. BUILDPLAN v4.2 §24: deliverables
"collector, point-in-time timestamps/features/snapshots, archive budget"; gate
"gap/no-lookahead/revisions/replay ผ่าน". Rules come from §5.1, §5.2, §4.4 and §20.

## What this environment can reach (checked 2026-10-15)

| Source | Result |
| --- | --- |
| data.binance.vision (spot and futures/um archive, with .CHECKSUM) | reachable |
| fapi.binance.com REST | HTTP 451 |
| api.binance.com, data-api.binance.vision, stream/fstream websockets | no connection |

So the Phase 2 collector reads the public archive only: 1h klines (perp and
spot), 1h premium-index klines and monthly funding prints. A day appears about
8 hours after it closes. Tick, L1 and trade feeds (§4.4) cannot be collected
from here; the archive budget below measures what is collected and states that
gap. A live feed is a later adapter behind the same raw store.

## Deliverables

1. **Raw store** (`trading.data.raw_store`): each downloaded file is kept as
   bytes under its sha256 and recorded in an append-only, hash-chained log with
   `source_key` (the URL), `received_at` (when the bytes arrived),
   `recorded_at`, size, checksum status and `revision`. The same bytes again
   add nothing. Different bytes under the same `source_key` add revision n+1;
   nothing is overwritten or deleted.
2. **Collector** (`trading.data.collector`): downloads archive files for a
   date range, verifies each against its published .CHECKSUM, stores them, and
   reports MISSING, STORED, DUPLICATE or REVISION per file. A checksum mismatch
   stores nothing and fails the file.
3. **Point-in-time bars** (`trading.data.point_in_time`): bars are
   `[open_at, close_at)`; `available_at = max(close_at, received_at)`.
   A view `as_of` t uses only the latest revision received by t and only bars
   available by t. Gaps are reported and never forward-filled. 4h bars are
   built only from four closed 1h bars (00/04/08/12/16/20 UTC).
4. **Snapshots** (`trading.data.snapshot`): symbol, as_of, feature_version,
   last closed bar per timeframe, features from closed bars only, quality flags
   (gaps, stale data, missing funding), raw pointers (source_key, sha256,
   revision) and a canonical JSON sha256. Replaying a snapshot from the raw
   store at its as_of must give the same hash.
5. **Archive budget** (`scripts/data_budget.py`): bytes per dataset per day
   from the store, projected to 30 days and a year, with the missing tick/L1
   feeds stated.
6. **Storage location**: files on disk (blobs, `raw_log.jsonl`,
   `snapshots.jsonl`), per §20.1 ("Research spike ใช้ local datasets ... ก่อน").
   The `trading.market_snapshots` table comes with the PR that first writes
   snapshots online, because supabase/README.md requires every migration file
   to be applied and recorded in the same PR, and this PR changes nothing in
   the Supabase project.

## Acceptance tests (written to pass or fail, not tuned afterwards)

- **Gap**: a missing day leaves a reported gap; no bar is invented; a 4h bar
  with a missing 1h bar is not built.
- **No-lookahead**: a snapshot at t excludes bars that close after t, and
  excludes bars of files received after t even when the bar closed before t.
- **Revisions**: corrected bytes for a file become revision 2; a snapshot
  taken before revision 2 arrived replays with revision 1 and the same hash; a
  snapshot after it uses revision 2.
- **Replay**: rebuilding every recorded snapshot from the raw store gives the
  same hash; a changed blob or log line is detected by `verify()`.
- **Checksum**: a mismatched file is refused and leaves no record.

## Not in this PR

The paper book (pathA-v1.1) keeps its own frozen data path and is not moved
onto the new store; that would change the book under test. No DB writes to the
Supabase project, no deploy, no live feed, no order adapter.

Added during implementation: a snapshot whose stored history starts after its
30-day lookback is flagged `short_history` and marked incomplete, since EMA
features on a shorter history differ.

## First run (2026-10-15)

`scripts/data_collect.py --start 2026-08-15 --end 2026-10-14 --snapshot` into
`/mnt/project-files/data-archive/btcusdt`:

- 61 daily files each of perp 1h, spot 1h and premium index 1h, 2 monthly
  funding files (October not published yet: MISSING), no revisions,
  `verify()` clean.
- Snapshot as of 2026-10-15 15:24 UTC: complete, last actual funding print
  2026-09-30 16:00, all 7 window days are proxy (funding_policy.v1), replay
  hash matched.
- Budget (`scripts/data_budget.py`): about 4.0 KB of compressed archive per day
  for all four datasets, about 1.5 MB a year, plus about 1.9 KB of log per
  file. Tick, L1, depth and mark streams are not measured because they cannot
  be reached from here.
