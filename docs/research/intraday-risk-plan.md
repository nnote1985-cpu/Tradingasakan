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

## Results (added after the run; nothing above was changed)

`research/spike/run_intraday_risk.py`, report in `reports/intraday-risk/report.json` (not committed). The
stop-free hourly simulator reproduces `run_r7.simulate`: R7 +103.4% vs +103.4%, R11 +221.1% vs +221.0%.

**Q1.** Checking once a day misses little drawdown, because the books are 1x:

| | max DD at daily closes | at hourly closes | at worst intrabar price | worst drop within a day | least equity at worst price |
|---|---|---|---|---|---|
| R7 | 55.0% | 55.7% | 56.0% | 16.6% | 0.91 × entry equity |
| R11 | 39.4% | 40.7% | 42.1% | 18.3% | 0.91 × entry equity |

Liquidation was never close (it needs equity at 0.10 × entry). The first 15% alert and 25% HALT trip
on the same day whether checked hourly or daily, except R11's HALT: hourly 2023-06-20 18:00, daily not until
2023-08-17 (the close stayed just under 25% on 2023-06-20).

**The larger finding is the HALT itself.** Both books' development drawdowns (55% / 39%) are far past the
`spot.v1` 25% HALT, which is absorbing until Note resumes. With it, at base costs:

| | no HALT | HALT checked daily | HALT checked hourly |
|---|---|---|---|
| R7 net / Sharpe / DD | +103.4% / 0.43 / 55.7% | +50.1% / 0.44 / 27.6% | +53.6% / 0.47 / 25.9% |
| R11 net / Sharpe / DD | +221.1% / 0.71 / 40.7% | +40.5% / 0.35 / 32.6% | +53.4% / 0.46 / 25.5% |

Hourly checking holds the HALT near 25%; daily checking let R11 reach 32.6%. Both stop the book in mid-2023.

**Q2.** Tightest harmless stop: **25%** from entry, which never fired. 20% fired once on R11 (Sharpe −0.07,
net −28 pp); 10% and 15% hurt both books; 5% helped R7 (+34 pp, 14 stops) and badly hurt R11 (−148 pp),
so the tight end is noise, not a rule. Costs ×2 gives the same ordering.

Reading: a 1x book needs an exchange stop only as a catastrophe guard (≥25% from entry); continuous
monitoring matters mostly for the HALT, which daily checking can overshoot by several points.
