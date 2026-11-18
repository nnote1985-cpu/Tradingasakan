# R20: layer 1 = R10a on top of the carry base (pre-declared)

Written 2026-11-17, before any R20 backtest was run. Note approved ("ลองเลย") testing the first profit
layer on R19's carry base with the layer acceptance proposed there.

## Construction

Base = R19 K0 (always-on carry: long spot + short perp of equal quantity). Layer = R10a's spot
weight w ∈ {0, 0.5, 1} (band on SMA100, half when funding is hot) scaled by a layer size s.
Each day: spot weight = s·w + 0.5·(1 − s·w), perp weight = −0.5·(1 − s·w). So the part of equity used
by R10a is held as plain spot (unhedged); the rest stays in carry. Simulated with `run_r15.simulate`
(same costs, funding, rebalance, liquidation as R15/R19).

Layer sizes, fixed now: **s = 1.0, 0.5, 0.25**. The largest s that passes on dev is the candidate;
C1 is run for all three (no selection on C1).

## Acceptance (per s, both windows)

1. Combined net **and** Sharpe above the base alone (K0) on the same window.
2. Layer contribution (combined quarterly P&L − base quarterly P&L, as % of the combined equity at the
   quarter start) ≥ −5% in **every** quarter.
3. Combined ≥ 70% of quarters non-negative and max DD ≤ 15% (daily closes).

Windows: dev 2022-05-01 → 2026-01-01, C1 2020-04-15 → 2022-01-01. C2 not used.
Reported: quarterly table (base, combined, layer contribution), annual yield, DD, worst quarter.

## Result (run once 2026-11-17; `/mnt/project-files/r20-results/`)

Base K0: dev +14.7% / Sharpe 11.4 / DD 0.2%; C1 +29.6% / 6.4 / 1.2%.

| s | dev net (per yr) | dev Sharpe / DD | dev quarters ≥ 0 | worst layer qtr | C1 net (per yr) | C1 Sharpe / DD | C1 quarters ≥ 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1.0 | +257% (41%) | 1.03 / 25.5% | 8/15 | −13.1% | +488% (181%) | 2.02 / 22.8% | 5/7 |
| 0.5 | +124% (25%) | 1.24 / 12.8% | 8/15 | −6.8% | +192% (87%) | 2.38 / 12.6% | 6/7 |
| 0.25 | +61% (14%) | 1.47 / 5.9% | 9/15 | −3.4% | +91% (46%) | 2.84 / 6.6% | 7/7 |

Dev by year (s = 0.25): 2022 −2.1%, 2023 +23.1%, 2024 +27.1%, 2025 +4.4%.

**Verdict: no size passes as declared.**
- Criterion 1 cannot be met by any directional layer: the base's Sharpe (6–11) comes from its tiny
  volatility, so "Sharpe above the base" was a design error in the criterion (declared, not changed
  here). Net is far above the base for every size.
- Criterion 3 fails on dev for every size: R10a's trend layer loses in 6–7 of 15 quarters (small
  losses, −0.2% to −2.9% at s = 0.25, big gains in 4 quarters). Losing quarters are inherent to trend
  following; only yearly results are mostly positive (one losing year, 2022, −2.1% at s = 0.25).
- Criterion 2 holds at s = 0.25 on both windows (worst layer quarter −3.4%).
Any change of criteria (e.g. Sharpe vs R10a instead of the base, or years instead of quarters) is
Note's call and would be a new, declared test.
