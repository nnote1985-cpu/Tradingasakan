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

## Result (run once, 2026-10-16, `research/spike/run_c1_band.py` @ d66586b; outputs in `/mnt/project-files/c1-band-results/`)

Data: 48 monthly files (klines + funding, 2020-01 → 2021-12), all VERIFIED; 17,544 hourly bars,
2,193 funding rows. 626 days in C1, no missing weights. Before the C1 run the script was checked on
2022–2025 data and reproduced R10 and R11 exactly.

2020-04-15 → 2022-01-01, 10,000 USDT:

| | Net | Sharpe | Max DD | Trades / flips |
| --- | --- | --- | --- | --- |
| path A (spot) | +347.8% | 1.759 | 27.4% | 188 |
| **R10a (spot)** | **+443.0%** | **1.930** | **23.2%** | 168 |
| R7 (perp) | +123.1% | 0.658 | 46.4% | 27 flips |
| **R11 (perp)** | **+246.9%** | **0.986** | **34.8%** | 7 flips |
| BTC buy & hold | +572.4% | 1.534 | 53.2% | 1 |

By year (net / Sharpe / max DD, 2020 from Apr 15 / 2021): path A +161.1% / 2.62 / 24.7%, +71.5% /
1.11 / 27.4%; R10a +203.1% / 3.01 / 17.0%, +79.2% / 1.15 / 23.2%; R7 +59.7% / 1.05 / 41.3%,
+39.7% / 0.44 / 46.4%; R11 +123.3% / 1.77 / 33.0%, +55.4% / 0.55 / 34.8%; B&H +321.3% / 3.46 /
17.6%, +59.6% / 0.58 / 53.2%. With costs ×2 the order is unchanged (path A 1.70 / 27.7%, R10a 1.90 /
23.7%, R7 0.63 / 47.1%, R11 0.98 / 35.1%).

R7/R11 detail: R11 paid 12,475 USDT net funding (R7 8,336), fees 178 (R7 350); short days still
lost money (log −0.196 vs R7 −0.407). Path A's funding overlay was active on 155 of 626 days.

**Verdict:**

- **R10a confirmed** (Sharpe 1.93 > 1.76, max DD 23.2% ≤ 27.4%, net > 0), better in both years, and
  meets Note's objective against buy & hold (Sharpe 1.93 > 1.53; max DD 23.2% vs 53.2%).
- **R11 confirmed against R7** (Sharpe 0.99 > 0.66, max DD 34.8% ≤ 46.4%), but it does **not** meet
  Note's objective against buy & hold (Sharpe 0.99 < 1.53, max DD 34.8% > ½ × 53.2%). Funding and
  the short side cost it in a bull market, as declared.

C1 is now spent. C2 (2026-01-01 → 2026-10-01) remains never simulated by any strategy.
