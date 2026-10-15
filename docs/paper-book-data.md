# Paper book data: market, funding and timing

Answers the owner's questions on the pathA-v1 manifest (2026-10-15). Numbers come from
`research/spike/check_pathA_data.py` (rules and signals from frozen/pathA-v1, nothing tuned);
output `/mnt/project-files/pathA-data-checks/report.json`.

## 1. Which market is simulated

| Part | Source |
| --- | --- |
| Book | **Spot**: USDT cash + BTC, no leverage, no funding paid or received, 0.10% fee + 2 bps slippage |
| Signals | USD-M BTCUSDT perpetual daily closes + perpetual funding |
| Fill, mark-to-market, buy & hold | **Perpetual bars as a proxy for spot** (1h open for fills, daily close for marks) |

Measured effect of the proxy: perp/spot − 1 on daily closes averaged −0.044% over the
holdout (max 0.069%, 341 days) and −0.053% in 2026-10-01..14. Re-running the holdout with the
same weights but spot bars for fills, marks and buy & hold: R5 −6.93% (perp-priced −6.90%),
buy & hold −16.52% (−16.51%). The perp-priced results stay as recorded and are labelled as
proxy-priced; the spot-priced run is reported beside them. Changing the book itself to spot
prices would be a new rules version, not an edit to pathA-v1.

## 2. Where funding comes from

- Up to the last complete month: Binance's monthly `fundingRate` archive (actual prints).
- The current month has no archive yet, so it is a **proxy, not actual funding**: estimated
  per 8h from 1h `premiumIndexKlines` with Binance's formula
  `F = mean(premium 1h closes) + clamp(0.01% − mean, −0.05%, +0.05%)`. Binance uses a
  time-weighted average of finer premium samples, so the hourly-close mean is an approximation.
  Every day that uses it is flagged, and the book's notes say "funding บางวันเป็นค่าประมาณ".
- For 2026-10-01..14 every day used the proxy. The REST API that serves actual prints refuses
  this environment (HTTP 451), so there is no API snapshot.
- Proxy vs actual, 2025-10..2026-09 (1,091 prints): mean absolute error 0.0027% per print,
  max 0.022%; 7-day mean used by the signal: error 0.0019%/day on average, max 0.007%/day,
  against a threshold of 0.09%/day. The hot/not-hot flag never differed on the 359 days
  compared (no day was hot in that year, so this checks the margin, not a flip).
- Decisions so far: 7-day mean 0.0035%/day on 2026-10-13 and 0.0031%/day on 10-14, about 25×
  below the threshold.
- No stale data is substituted: a day without funding prints or premium bars is NaN, the 7-day
  window then has fewer than 7 days, and no weight is suggested (the book records
  "ข้อมูลไม่พอ"). A day with only some of its prints would be summed short without a flag;
  the checks above found none in the period used. Archive files are used only after their published checksum matches.
- When the October archive is published, compare it with the proxy for the days the book used.

## 3. Timing evidence

Entries up to state.json sha256 `6257466a…` are **reconstructed**, not forward records; see
`/mnt/project-files/paper-trading/pathA-v1/provenance.md`. From the 2026-10-16 run on, the
daily job runs the frozen book through `scripts/paper_run.py`, which appends one hash-chained
line per run to `runs.jsonl` (start/finish, every fetched archive with its arrival time and
sha256, decisions and fills, state hash before/after) and anchors the line hash in
`trading.audit_events` for an independent server timestamp. No timestamp is back-filled.

Found while checking: `paper_trade.py` stamps `received_at` with the run's start time, a few
seconds before the data actually arrives. A run starting seconds before the top of an hour
could fill at that hour's open, earlier than possible. The 08:20 UTC schedule avoids it, and
`runs.jsonl` checks for it every run; a real fix needs a new rules version.
