# R19: a non-losing base: delta-neutral funding carry (pre-declared)

Written 2026-11-17, before any R19 backtest was run. Note's framing (2026-11-17, "ลองหน่อย"): the
purpose of futures is not to beat spot; first find a base that does not lose money over a set period
(not every trade), then layer profit strategies on it one at a time and re-test. R19 tests the base.
R15's F2 (carry with a funding threshold) is the starting point; it was seen on dev and C1 there, so
its variant here is not new evidence, only a fuller report.

## Variants (three)

Long BTC spot + short BTCUSDT perp of the same quantity (delta-neutral), simulated with R15's
one-account simulator (`run_r15.simulate`, unchanged): daily decisions, spot 0.10% + 2 bps, perp
0.05% + 2 bps, daily funding, legs rebalanced when they drift > 20% from target, liquidation of the perp
leg if the day's 1h high takes its margin (cash) to maintenance.

- **K0 always on:** spot 0.5 / perp −0.5 of equity from the first day to the end (perp leg 1x on its margin).
- **K1 funding filter (= R15 F2):** same legs, on when the 7-day mean funding > 0.005% per 8h, off
  when it is < 0, in between keep the state; flat (cash) when off.
- **K2 capital-efficient filter:** as K1 but spot 0.75 / perp −0.75 (perp leg 3x on 0.25 of equity).
  More yield per dollar, at the risk of a rally squeezing the short leg's margin.

Cash earns nothing (no USDT interest), to be conservative.

## Windows

dev 2022-05-01 → 2026-01-01; C1 2020-04-15 → 2022-01-01. C2 not used (its final check needs Note's OK).

## Acceptance (per variant, both windows)

1. **≥ 80% of calendar quarters non-negative** (partial quarters of ≥ 30 days count).
2. **Max drawdown ≤ 10%** on daily closes (worst intraday also reported).
3. Net > 0 after all costs; no liquidation.

## Reported

Quarterly table: P&L %, funding received, fees, and the rest (basis and rounding), losing quarters
and why; annual yield; capital split (spot / perp margin / idle cash) and days on. If a variant passes,
the report proposes which strategy to layer next and the same acceptance for that layer.
