# R16: three futures systems checked every hour (pre-declared)

Written 2026-11-17, **before any R16 backtest was run**. Note asked (2026-11-17) to keep working on
futures rather than pause, and to race three systems: A Trend Pullback, B Breakout, C Adaptive
(trend or mean reversion by regime). Signals are **checked** every hour; a trade happens only when a
signal fires, so entries must be selective. Each system runs in two modes: may hold overnight, and
intraday only (flat before the UTC day ends). Results are summarised per day.

Honest prior, declared now: R1/R2 (1h breakouts on perps) and the R13 H1 tight-stop EAs lost, mostly
to fees. R16 uses one simple parameter set per system, textbook values, nothing tuned afterwards.

## Common rules

- BTCUSDT USDⓈ-M perp 1h bars (regularized; a missing hour gives no signal and no fill). Signal at
  each hour's close; fill at the next hour's open. Long and short, **1x**: notional = equity at entry.
  One position at a time. A signal for the other side reverses at the same open.
- Costs: taker 0.05% + 2 bps slippage per side; costs ×2 also reported.
- Stop: fixed at entry ± 2 × ATR14 (H1, Wilder) of the signal bar, checked on each bar's high/low;
  filled at the stop (or the open if it gapped through) less slippage.
- Funding: Binance settles at 00:00, 08:00, 16:00 UTC. A position held at that timestamp (i.e. open
  before the bar that starts then) pays `qty × open price × rate` (short receives), with the real rate
  of that settlement.
- **Mode O (overnight allowed):** exits only by the rules below.
- **Mode I (intraday):** no entries from signals at the 22:00 or 23:00 bar close; any position is
  closed at the close of the 23:00 UTC bar (23:59:59). It never holds at 00:00, but can still cross
  the **08:00 and 16:00** settlements.
- 10,000 USDT, compounding, quantities not rounded.

## Systems (one parameter set each)

Higher timeframe = H4 bars built from H1, used only after the H4 bar has closed.

- **A Trend Pullback.** Trend up if H4 close > H4 EMA50, down if below. Long when trend up and
  RSI14 (H1) < 30; short when trend down and RSI14 > 70. Exit long when RSI14 > 55, or the H4 trend
  turns down, or after 48 hours (short: RSI14 < 45, trend turns up, 48 h). Plus the 2×ATR stop.
- **B Breakout.** Long when the H1 close > the highest high of the previous 48 bars (2 days); short
  when the close < the lowest low of the previous 48. Exit long when the close < the lowest low of
  the previous 24 bars (short: > the highest high of the previous 24). Plus the stop.
- **C Adaptive.** Regime from H4 ADX14: trend if ADX > 25, range if ADX < 20, otherwise keep the
  previous regime (start: range). Trend regime: B's entries. Range regime: Bollinger(20, 2) on H1,
  long when the close < lower band, short when the close > upper band. A position keeps the exit
  rules of the mode that opened it (B's channel exit, or the close crossing the 20-bar middle band).
  Plus the stop.

Six runs: A-O, A-I, B-O, B-I, C-O, C-I.

## Windows

1. dev 2022-05-01 → 2026-01-01 (perp data from 2022-01 for warm-up). Run all six, commit.
2. C1 2020-04-15 → 2022-01-01, once, all six (second screen; C1 was spent on R10a/R11).
   **C2 (2026) is not downloaded or used.**

## Benchmarks and pass (same as R15)

B&H spot and R10a (from R15's runs on the same windows). Per window, base costs:
A: Sharpe > R10a's and max DD ≤ R10a's + 5 points; B: net > 0 and (Sharpe > B&H's or max DD ≤ ½
B&H's); C: B still holds with costs ×2; no ruin. "Winner" = all on both windows.

## Reported per run

Per day (UTC, equity at each day's end): mean and median daily return, std, % up days / down days /
no-position days, average daily P&L in USDT, best and worst day. Also net, CAGR, Sharpe, max DD,
trades total and per month, win rate per trade, average hold, fees, funding paid and the settlements
crossed. Outputs `/mnt/project-files/r16-results/`.
