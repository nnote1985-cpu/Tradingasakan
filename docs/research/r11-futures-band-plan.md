# R11: R7 futures long/short with a ±2% hysteresis band (pre-declared)

Written 2026-10-16, before any R11 backtest was run. R10a (path A spot with a ±2% band around
SMA100) passed its screen on 2022–2025. Note asked whether to try the same band on R7 (BTC perp,
long above / short below daily SMA100) and chose "เริ่มเลย". Exploratory, on data already seen;
C1 and C2 are not used.

## Question

Does the R10a band reduce R7's whipsaw (each SMA100 cross flips long ↔ short, double-size trades)
enough to improve R7's risk-adjusted result?

Hypothesis: fewer flips save fees and wrong-way shorts in sideways phases. Counter-risk, declared
now: the band does not fix that R7's short days lost money overall (log −0.078 on development), and
later flips can cost in sharp moves.

## Data

`data/binance_um` (BTCUSDT USD-M perp 1h klines and funding, 2022-01-01 → 2025-12-31,
checksum-verified), the files path A and R8–R10 used. No 2020–2021 or 2026 bars are loaded, so the
band state cannot depend on C1 or C2.

## Rules (one trial)

Everything is R7 (`research/spike/run_r7.py`: daily bars, next-open execution, 1x, taker 0.05% +
2 bps per side, daily funding, liquidation check, 10,000 USDT), except the side:

- SMA100 of daily closes. On the first day with a valid SMA100, side = +1 if close > SMA100, −1 if
  close < SMA100, 0 if equal.
- After that, each day: if side ≠ +1 and close > SMA100 × 1.02 → +1 (long); if side ≠ −1 and
  close < SMA100 × 0.98 → −1 (short); otherwise keep the previous side. Invalid days keep it.

R7 itself is re-run on the same data as the base for comparison (it should reproduce its
development result, +103.4% / Sharpe 0.43 / DD 55.0%; any difference is reported).

## Window

R7's development set, 2022-04-12 → 2026-01-01. Also reported per year (2022 from April, 2023,
2024, 2025).

## Pass criteria

1. **Better than R7:** Sharpe(R11) > Sharpe(R7) **and** max DD(R11) ≤ max DD(R7) **and** net > 0.
2. Also reported against R7's own rule (net > 0 and Sharpe > buy & hold) and Note's objective.

Also reported: flips, days long/short, fees, funding, long-day vs short-day log return, results
with costs ×2. Passing makes R11 a candidate for a confirmation run (Note's separate approval) and
a futures paper book; it does not change the running futures-r7 book. A fail is recorded and not
retuned.
