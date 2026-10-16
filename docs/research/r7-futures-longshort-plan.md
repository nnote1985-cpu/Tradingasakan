# R7 plan: trend long/short on BTC perpetual (pre-declared)

Written 2026-10-16, **before any R7 backtest was run**. Owner request (Note,
2026-10-16): try a futures backtest now. Note chose "Long/short เทรนด์" on a
decision card: v2's daily SMA100 regime, but short below the SMA instead of
holding cash. Codes follow `results-register.md`.

## What we already know

- R3 (v2, long or cash on SMA100) beat buy & hold on BTC in development
  (Sharpe 1.03 vs 0.27) and lost to it in validation (0.98 vs 1.43). Its
  weakness is a strong uptrend with sharp dips, where it sells and buys back late.
- R1 and R2 (intraday breakouts on perps) failed. R6 (v1 on spot) failed development.
- Development below was seen by R1–R6, so passing it says little. Only the
  confirmation sets count.

## Hypothesis

In a falling market a short earns what the long-or-cash rule only avoids
losing. If BTC's daily trend persists enough, being short below SMA100 should
add return in bear phases (2022) at the cost of whipsaws in sideways phases,
and give a higher Sharpe than holding BTC.

## Data and partitions

BTCUSDT USDⓈ-M perpetual 1h klines and funding from data.binance.vision,
checksum-verified. Monthly archives start 2020-01, so C1 starts after the
100-day warm-up.

| Set | Range (UTC, end exclusive) | Status |
| --- | --- | --- |
| Development | 2022-04-12 → 2026-01-01 | Seen before by R1–R6. |
| Confirmation C1 | 2020-04-15 → 2022-01-01 | **Never simulated by any strategy** on this data. |
| Confirmation C2 | 2026-01-01 → 2026-10-01 | **Never simulated by any strategy.** |

Each confirmation set is simulated **once**, after the development result is
committed, and is then spent.

## Rules (fixed now)

- Daily bars on UTC days from 1h bars; a day with any missing hour is invalid
  and keeps the previous state (`trend_regime_v2.daily_bars`).
- Signal at each daily close: LONG if close > SMA100 of daily closes, SHORT if
  close < SMA100, unchanged if equal or invalid. FLAT before SMA100 exists.
- Trade at the next day's open. Every change of side closes the old position
  and opens the new one at **1x**: notional = equity at that open. No
  rebalancing while the side is unchanged. No stops, no take profit.
- Costs: taker 0.05% per side, 2 bps slippage per side.
- Funding: each day, the position pays `qty × daily close × sum of that day's
  funding rates` when long and receives it when short (negative rates reverse
  it). This is a daily approximation of the 8h settlements, as in R3's perp variant.
- Liquidation check: if a day's high (short) or low (long) would bring equity to
  10% of the equity at entry or less, the book is marked liquidated at that
  level and stops. At 1x this needs a ~90% move, so it is a safety check.
- 10,000 USDT. Quantities are not rounded (research approximation).

Only this one rule set is run. Nothing is tuned.

## Benchmark

Buy & hold BTC: buy once at the first open of the set with 0.10% fee and 2 bps
slippage, hold to the end, no funding. Perp prices stand in for spot prices
(basis about −0.04%, `results-register.md`). This is what holding BTC costs an
ordinary owner.

## Success criterion (fixed now)

Per set, after costs: **net return > 0 and Sharpe > buy & hold's Sharpe**
(daily log returns × √365). The drawdown alternative is not used. Drawdown is
still reported.

## Stopping rules

1. Run development.
2. If it fails, stop: report it and do not spend C1 or C2.
3. If it passes, commit the result, then run C1 and C2 once each.
4. R7 "beats the market" only if it passes **both** C1 and C2. Any other
   outcome is reported as is, with no rule changes and no re-runs.

## Reported for every run

Net, Sharpe, max drawdown, buy & hold net/Sharpe/drawdown, number of flips,
days long/short/flat, fees, funding paid (− = received), the return split
between long and short days, liquidation flag, and the daily equity and trades.
Output: `/mnt/project-files/r7-results/`.

## Known limits

- One symbol and one rule. Daily funding is an approximation.
- 2020–2021 was a strong bull market, so a short side is likely to cost there.
- Sharpe over 9 months (C2) is noisy.
- Passing would justify a SHADOW futures book, not real money.

## Development result (run 2026-10-16 at f64ef00, after this plan was committed)

`PYTHONPATH=research/spike uv run python research/spike/run_r7.py development`
→ `/mnt/project-files/r7-results/development.json`. Data: 59,160 1h bars and
7,395 funding rows, 2020-01 → 2026-09, all checksum-verified.

| 2022-04-12 → 2026-01-01 | Net | Sharpe | Max DD |
| --- | --- | --- | --- |
| R7 long/short | +103.4% | 0.43 | 55.0% |
| Buy & hold BTC | +121.5% | 0.42 | 62.0% |

- 57 position opens; 757 days long, 603 days short. Fees 904 USDT. Net funding
  paid 3,478 USDT (longs paid more than shorts received). No liquidation.
- Log return on long days +0.788, on short days −0.078: **the short side lost
  money over the period**, so the hypothesis (shorts add return in falling
  phases) is not supported here, even though the set passes.
- By the pre-declared rule this is a **pass** (net > 0 and Sharpe 0.43 > 0.42),
  by a margin far smaller than daily-return noise.
