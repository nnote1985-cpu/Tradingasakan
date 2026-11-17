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
