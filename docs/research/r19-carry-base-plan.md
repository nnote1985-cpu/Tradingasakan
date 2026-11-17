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

## Result (run once 2026-11-17; `/mnt/project-files/r19-results/`)

| Variant | dev net / per year | dev max DD (intraday) | dev quarters ≥ 0 | C1 net / per year | C1 max DD (intraday) | C1 quarters ≥ 0 |
| --- | --- | --- | --- | --- | --- | --- |
| K0 always on | +14.7% / 3.8% | 0.2% (2.1%) | 15/15 | +29.6% / 16.3% | 1.2% (10.4%) | 7/7 |
| K1 funding filter | +12.5% / 3.3% | 0.4% (1.0%) | 14/15 | +23.7% / 13.2% | 1.2% (1.7%) | 7/7 |
| K2 filter, 3x perp leg | +19.4% / 4.9% | 0.6% (1.5%) | 14/15 | +36.4% / 19.8% | 0.3% (1.6%) | 7/7 |

**All three pass.** The only losing quarter is 2022 Q2 for K1/K2 (−0.2% / −0.3%): entry fees on a
short stretch of funding. Funding was positive on average even through the 2022 bear, so K0 never had
a losing quarter. No liquidation (K2's 3x leg is rebalanced on 20% drift). Positions still open at the
window end are not charged exit costs (≈0.1% of equity).

Limits: the base earns roughly a cash-like yield in calm years (3–5% a year on dev, below what USDT
can earn in savings products); much more in the 2020–21 bull (13–20%). The worst intraday mark of K0 on
C1 (10.4%) comes from assuming spot and perp hit their highs at the same moment, an upper bound.
Basis was a small cost (single-digit USDT per quarter). Exchange risk (both legs on one exchange) is not
modelled.

## Next layer (proposal, not run)

Layer 1 = R10a on top of the base: when R10a is in, hold spot without the short hedge; when it is out,
run the carry (this is R15's F3). Acceptance for each new layer, on dev and C1: (1) combined net and
Sharpe above the base alone; (2) the layer's own loss ≤ 5% of equity in any quarter (sized to fit);
(3) combined ≥ 70% of quarters non-negative and max DD ≤ 15%. A layer that fails is dropped.
