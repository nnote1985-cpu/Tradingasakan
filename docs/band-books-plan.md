# Forward paper books for R10a (pathA-r10a) and R11 (futures-r11): plan and acceptance, declared before the code

Written 2026-10-16, before any book code. R10a (path A with a ±2% SMA100 band, spot) and R11 (R7
with the same band, perp) passed their screens on 2022–2025 and were confirmed against path A / R7
on C1 (`docs/research/c1-confirm-band-plan.md`). On 2026-10-16 Note chose "เริ่มเลย" on opening both
as forward paper books next to path A and R7, waiving the §16.5 two-candidate cap for these two
code-only books (no AI calls, no cost).

## Rules (unchanged from the research; no new parameter)

- **pathA-r10a.** Daily weight = band state × funding factor. Band state is a latch on daily perp
  closes vs SMA100: in when close > SMA100 × 1.02, out when close < SMA100 × 0.98, otherwise
  unchanged; it starts on the first day with a valid SMA100 as (close > SMA100). Funding factor is
  path A's (0.5 when the 7-day mean funding > 0.09%/day, else 1), from `risk_check.signals`. Everything
  else is pathA-v1.2: inputs, decision timing, spot fills and marks, fee 0.10% + 2 bps, `spot.v1`
  limits (alert at 5% day loss / 15% drawdown, HALT at 25%), resume only by a named record.
- **futures-r11.** Side latch on the same closes: long when close > SMA100 × 1.02, short when
  close < SMA100 × 0.98, otherwise unchanged; it starts as R7 does. Everything else is futures-r7:
  perp 1x, taker 0.05% + 2 bps, daily funding, `spot.v1` levels.

The latch is computed from the daily history each run fetches (pathA-r10a: 400 days, futures-r11:
300 days, as the existing scripts), not stored. A latch started inside the window can disagree with
one started earlier only until the first band crossing; the acceptance tests check this.

## Code

- `trading.strategies.sma_band`: the latch, one function for both books.
- `trading.shadow.band_books`: `BandSpotBook` (pathA-r10a) and `BandPerpBook` (futures-r11), the
  v1.2 and r7 books with their own rules versions, so a state file cannot be loaded by the wrong book.
  The two base classes read their rules version from a class attribute; their behaviour and their
  state files are unchanged.
- `scripts/paper_trade_r10a.py`, `scripts/paper_trade_r11.py`: the v1.2 and r7 scripts with the band
  rule. State under `/mnt/project-files/paper-trading/<version>/`.

The daily task, `paper_run.py` and the trading.* sync belong to the Phase 5 thread; this PR does not
touch them. After merge, frozen branches `frozen/pathA-r10a` and `frozen/futures-r11` are created and
the wiring is handed over.

## Acceptance tests

1. **Same rule as the research.** On 2022–2025 data the latch gives the same daily state as
   `run_r10.latch` (spot) and `run_r11.band_side` (perp) on every day.
2. **Window does not matter.** On 2022–2025 data, the latch computed from only the last 300 days
   before each day agrees with the full-history latch on that day, for every day from 2023-01-01.
3. **Books follow the research backtests.** On synthetic bars whose 01:00 open equals the daily open,
   with loss limits loosened, the band books make the same trades on the same days as
   `simulate_weights` with R10a weights and `run_r7.simulate` with R11 sides, and daily equity
   agrees within 1e-6 relative.
4. **State safety.** A state file saved by one book is refused by the other books (r10a vs v1.2,
   r11 vs r7), and save/load round-trips.
5. The existing v1.2 and r7 tests still pass.

## Not in this PR

No forward run, no daily-task change, no Supabase writes, nothing live.

## Results (added after the code; no rule above was changed)

- `tests/unit/test_band_books.py`, 7 tests, all pass: the latch equals `run_r10.latch` and
  `run_r11.band_side` on random series with missing days (3 seeds); a 400-day (spot) and 300-day
  (perp) window agrees with the full-history latch on every checked day; the spot book matches
  `simulate_weights` and the perp book matches `run_r7.simulate` with R11 sides on daily equity
  within 1e-6 and trade for trade; state files load only into the book that wrote them.
- Also checked once on real data (not a test, the files are not in CI): on 2022–2025 and on C1 the
  latch equals both research functions on every day, and the 400/300-day window latch equals the
  full-history latch on every day.
- Full suite: 438 tests pass; ruff and mypy clean. pathA-v1.2 and futures-r7 tests unchanged.
- Smoke run of both scripts against live data with throwaway state (2026-10-16): both open on the
  2026-10-15 close, R10a at 100% BTC and R11 long (close 82,195.40 above SMA100 × 1.02 = 75,124.51),
  the same targets the running v1.2 and r7 books opened with.
