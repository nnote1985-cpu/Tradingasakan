# R13: screen of open-source EAs on BTC/USDT dev data (pre-declared)

Written 2026-11-11, before any R13 backtest was run. Note asked (project chat, 2026-11-11) for a
5-step plan: pick 5–10 open-source EAs with different ideas, read their entry/exit and risk logic,
backtest them all on the same BTC/USDT data with fees and slippage, compare with the V1 rule-based
books, and later test whether AI helps. Note said "เริ่มเลย". This document covers steps 1–3 and
the dev-data part of step 4. **C1 is spent and C2 (2026-01 → 2026-10) is not touched.** At most two
finalists may go to C2, and only after Note's separate OK. The frozen branches are not changed.

## Source selection

Requirements: source code public, a licence that allows use and modification, no martingale,
grid, averaging-down or recovery logic, entry/exit logic self-contained enough to port faithfully.

| Source | Licence | Decision |
| --- | --- | --- |
| [logiccrafterdz/argus](https://github.com/logiccrafterdz/argus) — 20 MQL5 EAs (commit read 2026-11-11) | MIT | **Used.** One EA per file plus small shared helpers; fixed SL/TP or trailing stops, 1% risk per trade; no grid or martingale (searched). |
| [EA31337 strategies](https://github.com/EA31337/EA31337-strategies) | GPL-3.0 | Not used. Signals depend on the EA31337 framework (bitmask signal/filter methods, pip-based close rules, per-timeframe tuned set files); a faithful port means porting the framework. One module is explicitly `Oscillator_Martingale`. |
| MetaQuotes "MACD Sample" / "Moving Average" (bundled with MT5) | MetaQuotes copyright, no open-source licence | Not used: licence, and the source mirror is not reachable from this environment. |
| jesse-ai example strategies | MIT | Already in our strategy library (#9–#13); not EAs, left for a separate run. |

Argus EAs chosen (8 of 20), spread over ideas. The author's own backtest runs Donchian and
Smart Swing on H4 and the rest on H1 (`backtest/monte_carlo.py`); we use the same timeframes.

| # | EA (file) | Idea | TF | Entry (long side; short mirrors) | Exit |
| --- | --- | --- | --- | --- | --- |
| E1 | Donchian_Breakout | channel breakout | H4 | close1 > highest high of bars 2–21, close1 > EMA50 and EMA200, ADX14 ≥ 20 and rising | SL lowest low of bars 1–20, trailed to it each bar; TP 2.5R |
| E2 | SuperTrend_EMA | trend flip | H1 | SuperTrend(10, 3) flips up on bar 1, close1 > EMA200, ATR10 > 0.8 × SMA50(ATR10) | SL SuperTrend line, trailed with it; no TP |
| E3 | ADX_TrendStrength | trend strength | H1 | ADX14 ≥ 25 and rising, +DI > −DI, close1 > EMA200 | SL 1.5 × ATR14, TP 2.5R; close when ADX falls 2 bars in a row |
| E4 | TrendPullback | pullback in trend | H1 | EMA50 > EMA200 and EMA200 rising, higher low vs last swing low (30 bars), low2 ≤ EMA50, close1 > high2 | SL low1, TP 2R |
| E5 | Volatility_Squeeze | squeeze breakout | H1 | ≥ 5 bars with BB(20, 2) inside Keltner(EMA20 ± 1.5 ATR20), expansion candle (range and body > 1.5 × 20-bar mean), close1 crosses above upper BB, close1 > EMA100 | SL low1, TP 2R |
| E6 | Bollinger_MeanReversion | mean reversion | H1 | bar pierced lower BB(20, 2), then (within 15 bars) a close back inside with RSI14 < 30 | SL lowest low since the pierce, TP BB middle at entry |
| E7 | Hidden_Divergence | trend continuation | H1 | price above D1 EMA200; last two swing lows (radius 3, 60 bars) make a higher low while RSI14 makes a lower low; newest swing ≤ 6 bars old | SL swing low, TP 2R |
| E8 | Smart_Swing_Bias | HTF bias + retracement | H4 | D1 price > EMA200 and EMA50 > EMA200; latest swing leg (radius 3, 300 bars) has the high after the low; price in the 50–75% retracement zone; bullish rejection candle; max 1 trade per day | SL leg low, TP 2R |

Not chosen: the 7 session EAs (ORB_Session, ORB_Hybrid, Asian_Range_Fakeout, NY_Session_Reversal,
ICT_Killzone_Macro, PDH_PDL_BreakReversal, VWAP_MultiBand_Regime) are built on forex session
clocks in broker time; Liquidity_Sweep (2), SR_Breakout_Retest, PriceAction_SR and AVWAP need M15
or tick-level structure; the orchestrator is not a strategy. This keeps the screen to 8 trials.

## Port rules (fixed now)

- Port the **intended** logic on closed bars, with each EA's default inputs. Where the MQL5 code
  reads the still-forming bar (shift 0) or reads a non-series array in the wrong order (ADX
  `IsRising`), we use the last closed bar and the order the comments describe. Each case is listed
  in `research/spike/ea_ports.py`.
- Higher-timeframe values read at shift 0 (D1 EMA in E7 and E8) use the current price as the
  forming day's close (no look-ahead).
- Pip-based inputs (E7 10 pips, E8 30 pips, E4 5-pip SL buffer) are taken literally for a 2-digit
  BTCUSD symbol (1 pip = 0.01 USD), as the EA would behave on such a symbol in MT5.
- The orchestrator's regime/session globals are not set (stand-alone EA behaviour).
- Indicators follow MT5 definitions: EMA α = 2/(n+1); ATR = SMA of true range; RSI and ADX as in
  MT5 (`iRSI` Wilder smoothing; `iADX` EMA smoothing); Bollinger = SMA ± k × population std.
- H4 bars are UTC-aligned (00, 04, …); MT5 brokers align to broker time, so bar edges differ.

## Simulation (fixed now)

- Data: Binance USD-M BTCUSDT 1h (signals) and 1m (stop/target fills), data.binance.vision,
  2022-01-01 → 2025-12-31, the files registered in `results-register.md`. Indicators warm up from
  2022-01-01; evaluation window **2022-05-01 → 2026-01-01** (the R10 window).
- Signals are evaluated when a bar closes; orders fill at the next bar's open. SL/TP are checked
  on 1m bars; if one minute touches both, the stop counts first; a gap through a level fills at the
  minute's open. One position at a time, as in the EAs.
- **Primary book: BTC spot, long or flat** (short signals are ignored), 10,000 USDT, fee 0.10% per
  side, slippage 2 bps on market and target fills, 5 bps on stop fills — the same costs as R10a.
- **Secondary book: USD-M perp, long and short**, fee 0.05% + 2 bps (stops 5 bps), 8-hour funding
  paid/received on open notional, compared with R11.
- Sizing: **(a) EA native**: 1% of equity at risk from entry to SL, notional capped at 1× equity;
  **(b) full 1×**: whole equity per trade, same SL/TP. (a) is the EA as written; (b) puts it on the
  same exposure footing as R10a.
- Costs ×2 run for robustness.
- Benchmarks in the same window: R10a (spot), BTC buy & hold, and R11 (perp), computed by the
  existing R10/R11 code.

## Metrics and criteria (fixed now)

Daily equity at UTC close. Net return, Sharpe (daily log returns × √365), max DD, trades, win
rate, profit factor, average exposure, net per year, correlation of daily returns with R10a.

Per EA, primary book, native sizing, base costs:

1. **Profitable and beats B&H:** net > 0 and Sharpe > B&H Sharpe. (The "DD ≤ ½ B&H" alternative is
   not used: a 1%-risk EA passes it without skill.)
2. **Robust:** net > 0 with costs ×2, at least 30 trades, and net > 0 in at least 3 of the 4 years.
3. **Versus R10a:** Sharpe ≥ R10a's Sharpe in the same window (reported; not required to be a
   finalist, because a low-correlation EA could be a useful second book).

Ranking: by Sharpe of the primary book, native sizing, base costs. **Finalists:** at most two EAs
that pass 1 and 2, preferring those that also pass 3 or have daily-return correlation with R10a
below 0.5. If none passes, the result is "no finalist" and nothing goes to C2.

Every EA is run once with these rules. No parameter, timeframe or filter is changed after seeing
results; a changed rule would be a new, separately declared trial.

## Result (run 2026-11-11, `research/spike/run_r13.py`; outputs in `/mnt/project-files/r13-ea-screen/`)

One run of 64 (8 EAs × spot/perp × native/full × base/×2 costs). A second run after a lint-only
change gave a byte-identical `report.json`. The same code reproduces R10a in this window
(+244.8% / Sharpe 0.998 / DD 26.0%, as in R10). The 1m file hash matches `results-register.md`.

Primary book (BTC spot, long/flat, EA native 1% risk, fee 0.10% + 2 bps, stops 5 bps),
2022-05-01 → 2026-01-01, ranked by Sharpe:

| Rank | EA | Net | Sharpe | Max DD | Trades | Win | PF | In market | Net 2022/23/24/25 | Costs ×2 net | Corr R10a |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | E1 Donchian_Breakout (H4) | +34.0% | 1.35 | 4.4% | 65 | 46% | 2.56 | 31% | −2.4 / +31.5 / +3.2 / +1.1 | +29.5% | 0.52 |
| 2 | E2 SuperTrend_EMA | +9.0% | 0.26 | 12.8% | 158 | 33% | 1.12 | 15% | +1.1 / +1.7 / +17.5 / −9.7 | −10.9% | 0.30 |
| 3 | E8 Smart_Swing_Bias (H4) | +5.5% | 0.24 | 7.4% | 82 | 44% | 1.11 | 11% | 0.0 / −2.3 / +7.4 / +0.6 | −9.2% | 0.29 |
| 4 | E5 Volatility_Squeeze | −15.8% | −0.95 | 17.0% | 71 | 35% | 0.63 | 3% | all 4 years < 0 | −28.9% | 0.12 |
| 5 | E7 Hidden_Divergence | −46.8% | −1.84 | 47.5% | 326 | 32% | 0.65 | 14% | 0.0 / −27.6 / +4.8 / −29.9 | −75.5% | 0.36 |
| 6 | E6 Bollinger_MeanReversion | −48.0% | −1.85 | 49.8% | 171 | 21% | 0.48 | 9% | all 4 years < 0 | −64.5% | 0.12 |
| 7 | E4 TrendPullback | −83.3% | −3.60 | 83.4% | 718 | 33% | 0.52 | 15% | all 4 years < 0 | −97.2% | 0.27 |
| 8 | E3 ADX_TrendStrength | −88.2% | −3.60 | 88.2% | 1,137 | 27% | 0.64 | 15% | all 4 years < 0 | −99.0% | 0.34 |
| – | R10a (spot, daily) | +244.8% | 1.00 | 26.0% | 48 | | | | −12.5 / +89.1 / +97.5 / +5.5 | +235.3% | 1 |
| – | BTC buy & hold | +132.6% | 0.46 | 60.3% | 1 | | | 100% | −56.1 / +155.9 / +121.1 / −6.4 | | |

Same exposure as R10a (full 1× per trade): E1 +163.5% / Sharpe 0.99 / DD 23.6%; E8 +24.6% / 0.45 /
18.7%; all others negative. Perp long/short (native): E1 +28.1% / 0.92 / 9.1% (corr R10a 0.27);
all others ≤ +2.5%; R11 in this window +215.4% / 0.70 / 39.4%.

Criteria (spot, native, base costs): **E1 passes 1, 2 and 3** (net > 0, Sharpe 1.35 > B&H 0.46 and
≥ R10a 0.998; costs ×2 still +29.5%; 65 trades; 3 of 4 years positive). Correlation with R10a is
0.52, just above the 0.5 line. **Every other EA fails criterion 1 or 2.**

Reading:

- The H1 EAs with tight stops (E3, E4, E6, E7) trade 170–1,140 times; at 0.10% + 2 bps per side
  the round trip costs about a third of the risk unit, so they lose in every year. On perp fees
  (0.05%) they still lose. These EAs were written for forex CFD spreads, not BTC spot fees.
- E1's profit is concentrated in 2023 (+31.5% of +34.0%); 2024 and 2025 are near flat. Every exit
  is a trailing stop: the EA's trailing modify removes the 2.5R target (ported as written).
- E1 is the best of 8 tried, so its dev Sharpe is optimistic (selection over 8 trials). With full
  exposure it is close to R10a, not clearly better; its value would be as a second trend book with
  a different clock (H4 breakout, ~31% time in market).

**Finalist: E1 Donchian_Breakout (spot, native sizing) only.** No second EA qualifies. C2 is not
used; testing E1 on C2 needs Note's separate OK.
