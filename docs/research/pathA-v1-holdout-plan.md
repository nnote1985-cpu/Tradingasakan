# pathA-v1 holdout test (pre-declared, not yet run)

Written 2026-10-15, before the holdout was ever simulated. Runs only after the
owner approves spending the holdout.

- Rules: exactly `frozen/pathA-v1` (commit 42920ba): weight = (close > SMA100)
  × (0.5 if 7-day mean daily funding > 0.09% else 1); decided at the daily
  close. Backtest fill: next day's open (the live book fills after data
  receipt; the difference is reported, not optimised).
- Data: BTCUSDT, holdout partition 2025-01-25 → 2025-12-31 from configs/spike.json,
  SMA and funding warm-up taken from data before the partition.
- Costs: 0.10% fee + 2 bps per trade; benchmark BTC buy & hold with the same costs.
- Pass (owner's rule): net > 0 and (Sharpe > buy & hold Sharpe, or max drawdown
  ≤ half of buy & hold's).
- Reading the result: power for one year is about 46% and a no-skill rule passes
  about 29% of the time (docs/phase-1-plan.md), so a pass is weak support and a
  fail is not proof the rules are bad. Either way, nothing is tuned on the
  holdout afterwards, and pathA-v1 is not changed because of it.

## Result (run once, 2026-10-15, after the owner approved; signal code from frozen/pathA-v1)

| 2025-01-25 → 2025-12-31 | Net | Sharpe | Max DD |
| --- | --- | --- | --- |
| pathA-v1 | -6.9% | -0.32 | 18.0% |
| BTC buy & hold | -16.5% | -0.47 | 32.0% |

20 trades; out of BTC on 49% of days; never at 50% (funding never hot).

**Verdict: does not pass** the pre-declared rule, because net return is
negative. It lost less than buy & hold, with higher Sharpe and a drawdown of
18.0% against 32.0% (half would be 16.0%). The holdout is now spent; it is not
used again for tuning.
