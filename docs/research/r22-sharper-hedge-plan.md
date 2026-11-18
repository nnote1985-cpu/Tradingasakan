# R22: sharper hedge timing for 3a (pre-declared)

Written 2026-11-18, before any R22 backtest was run. Note asked whether a sharper short rule would
help, and pointed out that ETH-1 is still usable because no rule was changed after seeing it. Since
directional shorts failed (R15/R17), R22 tunes only **when the equal-size hedge opens and closes** in
3a (spot held; perp short of the same size neutralises it; the hedged part earns funding). Tuning
uses BTC dev and C1 only (both seen many times); the chosen rule then gets **one** run on ETH-1F.
ETH-2, ETH 2026 and BTC C2 stay untouched.

## Construction (as R20 s = 1.0)

Unhedged share u ∈ [0, 1]; spot weight = u + 0.5(1 − u), perp weight = −0.5(1 − u). R10a's funding
rule (half when 7-day funding is hot) applies in every variant. Costs, funding, rebalance and
liquidation as R15.

## Candidates (five, fixed now)

- **H0 current:** u = R10a weight (in above SMA100 × 1.02, out below SMA100 × 0.98).
- **H1 asymmetric exit:** in above SMA100 × 1.02, hedge as soon as the close < SMA100 (no lower band).
- **H2 faster trend:** same ±2% band on SMA50 instead of SMA100.
- **H3 two-stage:** H0, but while in, u is halved when the close < EMA20 (early weakness).
- **H4 drawdown hedge:** H0, but while in, u = 0 once the close is ≥ 10% below the highest close of the
  last 20 days; the hedge lifts when the close makes a new 20-day closing high (or the band exits).

## Selection (on BTC only)

Pick the candidate with the highest mean Sharpe over BTC dev and C1, provided its max DD is no more
than H0's + 2 points on both. If none beats H0, H0 stays and nothing goes to ETH.

## Validation (one run)

Chosen rule vs H0 on ETH-1F (2020-04-15 → 2022-01-01). "Better" = higher Sharpe and DD no worse
than H0 + 2 points on ETH-1F too. Reported: per year, quarters ≥ 0, number of hedge switches.
Overfitting read: the gain must show on all three windows (BTC dev, BTC C1, ETH-1F); a gain on one
BTC window only, or a gain that is gone on ETH, is reported as likely overfitting.

## Result on BTC (run once 2026-11-18; `/mnt/project-files/r22-results/`)

| Rule | dev per yr / DD / Sharpe | C1 per yr / DD / Sharpe | hedge switches dev / C1 |
| --- | --- | --- | --- |
| H0 current | +41% / 25.5% / 1.03 | +181% / 22.8% / 2.02 | 26 / 31 |
| H1 asymmetric exit | +38% / 32.8% / 0.99 | +135% / 27.3% / 1.73 | 36 / 45 |
| H2 SMA50 band | +36% / 29.3% / 0.94 | +224% / 19.2% / 2.48 | 42 / 35 |
| H3 two-stage | +34% / 24.4% / 0.98 | +148% / 21.6% / 2.03 | 102 / 86 |
| H4 drawdown hedge | +30% / 23.4% / 0.88 | +97% / 25.6% / 1.74 | 33 / 35 |

**Verdict: H0 stays.** Every sharper rule is worse than H0 on dev. H2 has the best mean Sharpe only
because of C1 (2.48), but its dev DD (29.3%) breaks the +2-point limit and its dev Sharpe is lower:
a gain on one window only, the overfitting pattern this plan warned about. Per the plan, nothing was
run on ETH, so **ETH-1F is still unused by any hedge-timing rule**. ETH-2, ETH 2026, BTC C2 untouched.
