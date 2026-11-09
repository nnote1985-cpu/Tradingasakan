# Data patches and continued book versions

## Why

data.binance.vision never published the BTCUSDT perpetual (futures/um) 1d and 1h klines for
2026-10-28. The daily files return 404, and the October monthly files skip the day (30 of 31 days).
The frozen books read only the archive. After that date:

- the spot books (pathA-v1.1, v1.2, p5-r1, p5-r2, r10a) recorded `no_decision` every day, because
  their SMA100 window has a hole and would keep it for about 100 days;
- the perp books (futures-r7, futures-r11) stopped at 2026-10-27 and wait for the missing day forever.

Frozen code is never changed. On 2026-11-09 Note chose to fill the day and start new versions that
continue from each book's current state.

## The patch: `configs/data-patches/gap1.json`

The archive does publish that day's trades file (`futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-10-28.zip`).
`scripts/build_gap_patch.py` rebuilds the missing 1d bar and the 24 1h bars from it:

- open, high, low and close come from the trade prices;
- volume, quote volume and taker-buy volumes are sums over the bar;
- count is the number of trades.

Before the patch is written, the script runs the same method on 2026-10-27, a day the archive has.
The rebuilt 1d bar and all 24 1h bars must equal the archive's in every column, or the script stops.
Every file is checked against its published `.CHECKSUM`. The patch records:

- the source file's URL and sha256;
- the check day;
- the rows.

The rebuilt 10-28 bar is O 80,868.4, H 81,800.0, L 80,355.4, C 81,308.8. The spot close that day was
81,343.5 and the index close was 81,345.3. Funding needs no patch: the October monthly fundingRate
file has all three 10-28 prints.

The patch was not built from the Binance REST API. REST answers 451 from the cloud, and the archive's
own trades file gives the same bars with a published checksum.

## Rules

- A patch row is used only where the archive file has no row with that open_time. An archive row
  always wins, so the patch becomes a no-op if Binance ever publishes the day.
- Only the patched day's daily file and its month's monthly file are touched (1d and 1h perp klines).
- Every patched file is logged in runs.jsonl `fetched[].patched` with three things:
  - the archive sha256;
  - the patch id and sha256;
  - the number of rows added.

  The logged `sha256` is the hash of the patched bytes the book read.
- A patch file is fixed once a version uses it. If the file changes, `paper_run.py` refuses to run,
  and a changed patch must be a new version.

## Continued versions: `<book>-g1`

`scripts/continue_book.py --book <book> --as g1` makes `paper-trading/<book>-g1/`:

- `state.json` is a byte copy of the base book's state. It is never edited.
- `continued_from.json` records:
  - the base state's sha256;
  - the base runs.jsonl's line count and last line hash;
  - the base book's last code commit;
  - the patch id and sha256.
- The base book gets `superseded_by.json`, and `scripts/paper_run.py` refuses to run it again. Its
  other files stay as they are.

The continued book runs the base book's frozen code from its frozen worktree, through main's recorder:

    cd /home/user/frozen/<book>
    SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python /home/user/wt-main/scripts/paper_run.py --book <book> --continuation g1

It writes its own runs.jsonl chain and its own `trading.audit_events` rows (target `<book>-g1`).
`trading_sync_sql.py` and `supabase_sync_sql.py` mirror it under its own name (`<book>-g1/paper` and so
on), with the base book's frozen commit and `frozen/<book>` as its ref. The base book's mirrored rows
stay as they were.

### What the continued versions do

- Decisions the base book already recorded (the `no_decision` days 2026-10-28 to 2026-11-07) are
  kept as they are and are never recomputed. The spot books simply decide again from the next close.
- The perp books process 2026-10-28 onward from where they stopped, using the patched bars, one day
  at a time, as they do after any archive delay.
- Evidence after the continuation point comes from the `-g1` version and is reported separately from
  the base book's.

## Risk check

`scripts/risk_check.py --data-patch gap1` applies the same patch. The JSON gains `data_patch` (id,
sha256, days, files patched), and the Thai summary says which day was filled.
