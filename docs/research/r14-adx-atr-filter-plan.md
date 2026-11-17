# R14: ADX / ATR filters on R10a (pre-declared)

Written 2026-11-17, before any R14 backtest was run. Note asked to try ("ลองหน่อย") ATR+ADX as an
extra filter on R10a. Exploratory screen on data already seen; it cannot prove an edge. C2
(2026-01 → 2026-10) is not used.

## Base

R10a, unchanged (`run_r10.py`): sticky trend state on daily BTCUSDT closes, in above SMA100 × 1.02,
out below SMA100 × 0.98, weight = state × (0.5 if funding hot else 1). Spot model, perp prices as
proxy, decided at day d close, filled at day d+1 open (`simulate_weights`), fee 0.10% + 2 bps.

## Features (known at UTC day d close)

Daily high/low = max/min of the 24 regularized 1h bars of the day (NaN unless all 24 are valid,
same as `daily_bars`). TR = max(H−L, |H−C_prev|, |L−C_prev|). ATR14 = Wilder average of TR
(EWM alpha 1/14, 14 valid days needed). ADX14 = standard Wilder ADX from +DM/−DM and ATR14.

## Variants (three, nothing else)

- **R14a ADX>20 entry gate:** R10a, but a switch from OUT to IN also needs ADX14 > 20. Exit unchanged.
- **R14b ADX>25 entry gate:** same with 25. 20 and 25 are Wilder's textbook levels, not tuned.
- **R14c ATR band:** replace the fixed ±2% with ±1 × ATR14: in when close > SMA100 + ATR14, out
  when close < SMA100 − ATR14. Multiplier 1 is not tuned.

NaN ADX → no new entry that day; NaN ATR → state kept. ATR-based position sizing is not tested:
it is volatility targeting, which already failed as R9b/R12.

## Windows

- dev: 2022-05-01 → 2026-01-01 (R10's window).
- C1: 2020-04-15 → 2022-01-01 (already spent on R10a's confirmation, so here it is a second screen,
  not a confirmation).

## Pass (per variant, both windows, base costs)

Sharpe(R14x) > Sharpe(R10a) **and** max DD(R14x) ≤ max DD(R10a) **and** net > 0, on dev **and** C1.
Also reported: costs ×2, per-year, trades, days in. A variant that fails is recorded and not
retuned. One that passes is only a candidate for C2 (Note's separate OK) or a forward paper book.
