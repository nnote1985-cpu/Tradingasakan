# Intraday risk of R7 / R11: plan, declared before the code

Written 2026-10-17, before any code or result. Note asked whether checking risk once a day is too
risky for the futures books ("ลองดูก่อนก็ได้"). This is a **measurement plus an exploratory grid on
development data only**. It changes no running book, picks no new candidate, and spends no
confirmation set (C1 is spent; C2 2026-01→10 stays unspent: no bar from 2026 is loaded).

## Data and baseline

- BTCUSDT perp 1h klines and funding from `data/binance_um` (2022-01-01 → 2025-12-31), window
  2022-04-12 → 2026-01-01, as `run_r11.py`.
- Books: R7 (`run_r7.side_state`) and R11 (`run_r11.band_side`), base costs (taker 0.05% + 2 bps)
  and costs ×2, 1x, the `run_r7.simulate` rules (fill at the daily 00:00 open after the signal).
- Kline trade prices stand in for the mark price (a real exchange stop and liquidation use mark).

## Q1. How much does a daily-close check miss? (measurement, no pass/fail)

For each book, from the unchanged `run_r7.simulate` positions, mark equity every hour:

1. Max drawdown at daily closes (what the paper books see) vs at hourly closes vs at the worst
   intrabar price (low for long, high for short).
2. Worst adverse move within one day while in a position, and the worst move from entry price.
3. `spot.v1` levels: the first time the 25% HALT and the 15% alert would trip when checked hourly vs
   at daily closes, and the extra drawdown between the two (the cost of checking once a day).
4. Liquidation: the least margin buffer reached (equity / entry equity at the worst intrabar price).

## Q2. Exchange stop distances (exploratory)

Variant: at each entry a reduce-only stop at k% adverse from the entry price. Within the day it
fills on the first 1h bar whose low (long) / high (short) reaches the stop, at the stop price, or at
that bar's open if the bar opened through it, with 2 bps slippage and the taker fee. After a stop the
book stays flat until the target side changes (no re-entry on the same signal). Funding is paid per
funding event while in a position. Grid: k ∈ {5, 10, 15, 20, 25}%. Also reported: an hourly-checked
25% equity-drawdown HALT (absorbing, flat to the end), against the daily-checked one.

The stop-free run of this hourly simulator must reproduce `run_r7.simulate` (net within 0.5 pp;
funding per event instead of per day is the only difference) or Q2 is not reported.

Reported per k: net, Sharpe, max drawdown (hourly marks), number of stops. **Declared reading:** a
distance is "harmless" if, for both R7 and R11 at base costs, Sharpe drops by at most 0.05 and net by
at most 2 percentage points vs no stop. The answer is the tightest harmless k (or "none").

This is a search over 5 values on data already used to pick R7/R11, so a harmless k is a hint for
the live design, not evidence. Using a stop in a forward book would be a new version with its own
pre-declared plan.
