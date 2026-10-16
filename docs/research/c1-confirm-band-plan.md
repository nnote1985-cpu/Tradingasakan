# Confirmation on C1: R10a vs path A (spot) and R11 vs R7 (futures) (pre-declared)

Written 2026-10-16, **before any 2020–2021 data was downloaded for this run**. Note approved
spending C1 ("เริ่มเลย") after R10a (path A with a ±2% SMA100 band) and R11 (R7 with the same band)
passed their screens on 2022–2025, data that had been seen before. C1 has never been simulated by
any strategy (`results-register.md`, R7 plan). After this run C1 counts as seen; C2
(2026-01-01 → 2026-10-01) stays unspent.

## Data

BTCUSDT USD-M perpetual 1h klines and funding from data.binance.vision, monthly archives
2020-01 → 2021-12 only, every zip checked against its `.CHECKSUM`
(`trading.market_data.binance_public.download`), into `data/binance_um_c1/`. Nothing after
2021-12-31 is downloaded or loaded, so C2 cannot leak in. The months before 2020-04-15 only warm up
SMA100 and are not scored.

## Window

C1 as defined in the R7 plan: **2020-04-15 → 2022-01-01**. Also reported per year (2020 from
April 15, 2021).

## Strategies (all rules exactly as committed; nothing changed)

| | Rule source | Model |
| --- | --- | --- |
| path A | `risk_check.signals` weight (R5 / pathA-v1) | spot, `funding_overlay_v3.simulate_weights`, fee 0.10% + 2 bps |
| R10a | `run_r10.latch` band ±2% × funding factor | same as path A |
| R7 | `run_r7.side_state`, `run_r7.simulate` | perp 1x, taker 0.05% + 2 bps, daily funding |
| R11 | `run_r11.band_side` with `run_r7.simulate` | same as R7 |
| buy & hold | path A's and R7's own benchmark functions | as in each script |

## Pass criteria (fixed now)

1. **R10a confirmed** if on C1: Sharpe(R10a) > Sharpe(path A) **and** max DD(R10a) ≤ max DD(path A)
   **and** net(R10a) > 0.
2. **R11 confirmed** if on C1: Sharpe(R11) > Sharpe(R7) **and** max DD(R11) ≤ max DD(R7) **and**
   net(R11) > 0.
3. Reported for all four, not deciding: Note's objective against buy & hold (net > 0 and Sharpe >
   B&H or max DD ≤ ½ B&H), trades, costs ×2, per-year results.

Known before running: 2020–2021 was a strong bull market, which favours buy & hold and penalises
R7/R11's short side.

## What follows

- Confirmed → the strategy is a candidate for a new forward paper book (after the Phase 5 day-28
  review, per the §16.5 two-experiment cap), with C2 kept as a further check if Note wants it.
- Not confirmed → recorded as "passed screen, failed C1"; no rule changes and no re-run on C1.
- The running books (pathA-v1.1, v1.2, p5-r1, p5-r2, futures-r7) are not changed either way.
