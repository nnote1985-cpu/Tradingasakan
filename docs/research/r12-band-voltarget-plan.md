# R12: R10a band plus R9b volatility sizing (pre-declared)

Written 2026-10-16, before any R12 backtest was run. Note asked why R10a/R11 drawdowns are still
high; the answer (drawdown episodes in Mar–Oct 2024, Apr–Oct 2023, Aug–Oct 2025: slow SMA100 exit
plus sideways whipsaws) led to proposing R10a combined with R9b's volatility sizing, and Note chose
"เริ่มเลย". Exploratory, on data already seen; C1 and C2 are not used.

## Question

Does scaling R10a's BTC weight down when 30-day volatility is above its one-year median lower
R10a's drawdown without giving up its risk-adjusted return?

Hypothesis: the worst drawdowns start with a high-volatility fall from a peak, so the vol scale
holds less BTC at those times. Counter-risk: R9b on path A cut drawdown but lowered Sharpe slightly
and quadrupled trades.

## Data

`data/binance_um` (BTCUSDT perp 1h klines and funding, 2022-01-01 → 2025-12-31), as R9/R10.

## Rule (one trial)

Weight_d = R10a state_d × funding factor_d × s_d, where

- R10a state and funding factor are exactly as in `run_r10.py` (band ±2% latch on SMA100; 0.5 if
  7-day mean funding > 0.09%/day else 1);
- s_d is exactly R9b's scale from `run_r9.py`: min(1, median of vol_30d over the last 365 days
  (≥ 180 valid) / vol_30d), rounded down to a multiple of 0.25, floor 0.25; 1 when not computable.

Spot model, decided at day d's close, filled at day d+1's open, 10,000 USDT, fee 0.10% + 2 bps.

## Window

2022-07-01 → 2026-01-01 (R9's window, so the one-year median is ready), with per-year results.
R10a and path A are re-run on the same window as comparisons.

## Pass criteria

1. **Better than R10a (primary):** Sharpe(R12) > Sharpe(R10a) **and** max DD(R12) ≤ max DD(R10a)
   **and** net > 0.
2. **Drawdown-first (secondary, declared now because Note asked about drawdown):** max DD(R12) at
   least 5 points below R10a's **and** Sharpe(R12) ≥ Sharpe(R10a) − 0.05 **and** net > 0. Passing
   only this one is reported as a trade-off, not as "better".

Also reported: Note's objective vs buy & hold, trades, per-year results, costs ×2, and the worst
three drawdown episodes. Passing makes R12 a candidate for confirmation (Note's separate approval).
A fail is recorded and not retuned.

## Result (run once, 2026-10-16, `research/spike/run_r12.py`; outputs in `/mnt/project-files/r12-results/`)

1,280 days, no missing weights. The vol scale cut the weight on 306 days while R10a held BTC.

2022-07-01 → 2026-01-01, 10,000 USDT, fee 0.10% + 2 bps:

| | Net | Sharpe | Max DD | Trades | Worst three drawdowns |
| --- | --- | --- | --- | --- | --- |
| path A | +176.5% | 0.843 | 35.1% | 82 | 35.1% (2024-04→10), 26.8% (2023-04→10), 19.0% (2023-02→03) |
| R10a band | +244.8% | 1.022 | 26.0% | 48 | 26.0% (2024-04→10), 21.7% (2025-08→10), 21.5% (2023-04→10) |
| R12 band + vol | +182.8% | 0.968 | 21.7% | 331 | 21.7% (2025-08→10), 21.5% (2023-04→10), 19.5% (2024-04→10) |
| BTC buy & hold | +339.2% | 0.880 | 35.4% | 1 | 35.4% (2022-08→11), 32.0% (2025-10→11), 28.1% (2025-01→04) |

By year (net / Sharpe / max DD), R10a vs R12: 2022 H2 −12.5% / −1.76 / 13.0% both; 2023 +89.1% /
1.59 / 21.5% vs +65.3% / 1.37 / 21.5%; 2024 +97.5% / 1.61 / 26.0% vs +87.0% / 1.83 / 19.5%; 2025
+5.5% / 0.21 / 21.7% vs +4.6% / 0.17 / 21.7%. With costs ×2: R10a 0.999 / 26.5%, R12 0.939 / 22.1%.

**Verdict: R12 fails both criteria.** Primary: Sharpe 0.968 < 1.022. Drawdown-first: max DD fell
4.3 points (needed 5) and Sharpe fell 0.054 (allowed 0.05), so it misses both limits narrowly. It
does meet Note's objective against buy & hold (Sharpe 0.968 > 0.880).

What it shows: vol sizing only helped the 2024 episode, which began with a high-volatility fall
(26.0% → 19.5%). The 2023 and 2025 drawdowns were slow, low-volatility sideways losses, where the
scale stays at 1 and changes nothing, so R12's max drawdown is set by them (21.7%). Not retuned;
C1 and C2 unspent.
