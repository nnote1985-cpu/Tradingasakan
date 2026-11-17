# R17: a short-specific sleeve beside R10a, with futures risk rules (pre-declared)

Written 2026-11-17, before any R17 backtest was run. Note asked (2026-11-17) whether every trade had
SL/TP and whether shorts had their own strategy, since long rules may not fit shorts. In R15 the
short leg had no SL/TP and mirrored the long exit; in R16 shorts were the long rules reversed with a
2×ATR stop. R17 tests asymmetric short rules, used only when R10a is in cash. Note added (2026-11-17) that
futures need a risk rule and SL/TP on every trade, since price can move far between hourly checks;
those rules are part of R17.

## Rules (one parameter set; daily bars from perp 1h, decided at day close, filled next open)

- **Regime:** daily close < SMA200 and SMA200 < SMA200 20 days ago, and R10a's state is out (cash).
- **Entry (short, perp):** either (a) sell the rally: the day's high ≥ EMA20 and the close < EMA20,
  or (b) breakdown: close < the lowest close of the previous 10 days.
- **Stop loss and take profit on every trade, resting on the exchange** (simulated as intrabar fills on
  1h high/low, never on the hourly close): SL = entry + 1.5 × ATR14 (daily, Wilder), TP = entry −
  2 × ATR14. Filled at the level (or at the open if the bar gapped through) with slippage. If one 1h
  bar touches both, the SL is assumed to fill first. **Time stop:** close at the 10th day's close.
  Also close at the next open if R10a turns in or the regime ends.
## Risk rules (declared now)

- **Size from the stop:** notional = 1% of account equity ÷ (SL distance / entry), so a stop-out loses
  about 1% of equity before costs. Notional is capped at 1× equity (leverage cap).
- **Daily loss cap:** if account equity falls 3% below its value at the start of the UTC day, no new
  sleeve entry that day.
- **Total loss cap (HALT):** if the sleeve's cumulative P&L falls 10% of starting equity below its peak,
  the sleeve stops trading for the rest of the window (R10a continues).
- **Liquidation distance:** reported per trade as the % move from entry to the liquidation price for
  isolated margin at the leverage set (notional / allocated margin, margin = notional at the cap, i.e.
  1x isolated), compared with the SL distance.

## Costs

- Costs: perp 0.05% + 2 bps per side; funding at the real 00/08/16 UTC settlements (short receives
  positive rates). Spot leg = R10a unchanged (spot costs). One account, 10,000 USDT.

## Windows

SMA200 needs 200 days of warm-up, so no C1 bars are loaded for dev.
- dev 2022-08-01 → 2026-01-01 (perp from 2022-01).
- C1 2020-08-01 → 2022-01-01 (perp from 2020-01), once, after dev is committed. C2 not used.

## Pass

1. The short sleeve alone: net P&L > 0 after costs on both windows.
2. R10a + sleeve vs R10a: Sharpe higher and max DD ≤ R10a's + 2 points, on both windows.
Reported: short trades, notional per trade, SL vs liquidation distance, cap triggers, win rate, avg hold, exits by stop / TP / time / regime, fees, funding, per year.
Few trades are expected (only 2022 is a real bear), so a pass is a direction, not proof.

## Dev result (run once 2026-11-17; `/mnt/project-files/r17-results/dev/`)

2022-08-01 → 2026-01-01: 16 short trades (37.5% win, 4 SL / 2 TP / 9 regime-or-time / 1 end), notional
avg 0.16× equity, SL distance avg 6.6%, worst trade −1.02% of equity. Sleeve alone −2.3% of equity
(funding received +0.20%, fees 0.26%). R10a + sleeve +236.9% / Sharpe 1.01 / DD 26.0% vs R10a
+244.8% / 1.03 / 26.0%. **Fails both criteria.** Daily cap and HALT never triggered. C1 next, unchanged.
