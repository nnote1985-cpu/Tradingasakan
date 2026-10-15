# R6 plan: v1 on spot, long only (pre-declared)

Written 2026-10-15, **before any R6 backtest was run**. Owner request
(Note, 2026-10-15): "พัฒนา v1 ให้ชนะตลาด" and move towards spot, with buy
options later. Codes follow `results-register.md` (R1 = v1).

## What we already know about v1 (R1) and its relatives

- R1 lost money in development (−4.8%) and validation (−1.4%) while
  buy & hold made +31% and +103%. Gross expectancy per trade was about zero
  and fees took the rest; the 2R target and 24h time exit cut every winner.
- It held BTC only 6–23% of the time at about 5% of equity per trade, so it
  could not keep up with a rising market even when its trades were right.
- R2 (4h Donchian, no target) fixed the winner-cutting but still failed
  validation. Its development profit came from three trades.

## Hypothesis

On spot, v1's entry (a 1h breakout inside a 4h uptrend) is a reasonable
time to own BTC. What lost was the exit and the size. Holding through the
trend, with no target and no 24h limit, and putting a meaningful part of
equity in, should let it collect uptrends and sit in cash during downtrends.
If that is true, it earns more per unit of risk than holding BTC all the time.

## Data and partitions

BTCUSDT **spot** 1h klines from data.binance.vision, checksum-verified.
Signals, fills, marks and buy & hold all use spot prices. There is no
funding (spot) and no shorting.

| Set | Range (UTC, end exclusive) | Status |
| --- | --- | --- |
| Development | 2022-04-12 → 2026-01-01 | Seen before (as perp) by R1–R5, so selection here can overfit. |
| Confirmation C1 | 2018-01-01 → 2022-01-01 | **Never simulated by any strategy.** Spot data back to 2017-08-17 covers warm-up. |
| Confirmation C2 | 2026-01-01 → 2026-10-01 | **Never simulated by any strategy.** |

Each confirmation set is simulated **once**, for the selected variant only,
after the selection below is committed. It is then spent like the 2025 holdout.

## Rules (fixed now)

Common to both variants:
- Entry signal: v1 LONG exactly as §6.2 (4h EMA50 > EMA200, 4h close > EMA200,
  1h close > previous 20-bar high, 1h close > EMA20(1h), warm-up and data
  valid). SHORT signals are ignored.
- Entry at the open of the next 1h bar after the signal bar closes (taker).
- Initial stop 2 × ATR14(1h) from the entry estimate, fixed for the trade.
- No take profit, no 24h time exit (safety time exit 365 days).
- Exit at the next 1h open after a closed 4h bar either closes below EMA200
  (v1's trend exit) or closes below the lowest low of the previous ten 4h bars
  (R2's channel exit); or at the stop (conservative intrabar, gap fills at the open).
- Cooldown 2h after an exit. One position at a time.
- Costs: spot taker fee 0.10% per side, slippage 2 bps entry/exit, 5 bps stop.
- Loss limits are recorded, not enforced (`record_only`), because the futures
  research profile (2% day / 8% drawdown) is not a spot policy; breaches are
  reported.
- 10,000 USDT, BTC step 0.00001, tick 0.01, min notional 10 (APPROXIMATE).

Variants (only these two):
- **R6a risk-sized**: risk 0.5% of equity per trade, 50 USDT cap, notional at
  most 100% of equity (no leverage).
- **R6b full**: buy with the whole equity (risk budget not binding; notional
  at most 100% of equity), stop and exits as above.

## Success criterion (fixed now)

Per set, after costs: **net return > 0 and Sharpe > buy & hold's Sharpe**
over the same period (daily returns × √365). Note's rule also allows
"max drawdown ≤ half of buy & hold"; that alternative is **not** used for R6,
because a strategy that is mostly in cash passes it without skill
(results-register footnote ²). Drawdown is still reported.

## Selection and stopping rules

1. Run R6a and R6b on development.
2. If neither passes on development, stop: report the failure and do not
   spend C1 or C2.
3. Otherwise select the variant that passes with the higher Sharpe; commit
   that choice; then run it once on C1 and once on C2.
4. R6 "beats the market" only if it passes on **both** C1 and C2. Any other
   outcome is reported as is, with no rule changes and no re-runs on C1/C2.

## Reported for every run

Net, Sharpe, max drawdown, buy & hold net/Sharpe/drawdown, trades, time in
market, average exposure, fees paid, loss-limit breaches, and the trade list.
Output: `/mnt/project-files/r6-results/`.

## Known limits

- One symbol; 2018–2021 is a different market (lower liquidity, larger moves).
- Sharpe on daily returns over 9 months (C2) is noisy.
- Development was seen before, so it says little on its own; only C1/C2 count.
