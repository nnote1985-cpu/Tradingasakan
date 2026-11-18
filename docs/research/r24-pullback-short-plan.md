# R24: Note's 4H-regime / 1H-pullback short (pre-declared)

Written 2026-11-18, before any R24 backtest was run. Rules are Note's (thread message 2026-11-18 21:50
Bangkok); where Note left something open, the choice made here is marked *(declared)*. Short only, BTC
perp, judged against cash (0%), like R23.

## Rules

**A. Regime (last closed 4H bar; 4H bars built from 1H).** ADX(14) > 25 and −DI > +DI (Wilder). Otherwise
NO_TRADE (no new entry; an open trade keeps its SL/TP).

**B. Pullback signal (on a closed 1H bar = the signal bar).** All of:
1. In the 24 1H bars before the signal bar there was a run of ≥ 3 consecutive closes below EMA20 *(declared window)*.
2. The signal bar's high ≥ EMA20 − 0.3 × ATR (touch or within 0.3 ATR). ATR = 1H ATR(14), Wilder.
3. Signal bar close < EMA20 and close < open.
4. RSI(14) of the signal bar between 35 and 55.
5. EMA20 − close ≤ 1 × ATR (not too far below EMA20).

Enter short at the next 1H open. One position at a time.

**C. Stop / target.** SL distance = max(1.5 × ATR, swing high + 0.2 × ATR − entry), swing high = highest
high of the last 10 closed 1H bars including the signal bar *(declared)*. Exchange-side SL and TP,
simulated on 1H high/low from the entry bar on; if one bar touches both, SL first (a TP-first bound is
also reported). TP = entry − R × SL distance. Variants *(declared)*:
- T1.5 / T2 / T3: TP at 1.5R / 2R / 3R, no other exit.
- T2X: TP 2R plus exit at the next 1H open after a 4H bar closes with −DI < +DI.

## Sizing, risk, costs

Notional = 1% of equity ÷ SL distance (stop ≈ −1% of equity), capped at **1x** or **2x** equity
(Note's comparison; with stops ~1–2% away the cap binds often, the count is reported). Daily cap 3%
(close, no new entry that UTC day), total cap 10% from peak (HALT). Fee 0.05% + 2 bps per side (taker,
also for TP, conservative); ×2 cost stress; zero-cost run reported to show the gross edge. Funding at real
settlements. Liquidation checked on the 1H high.

## Data

BTC 2018-04-01 → 2026-01-01 (spot 1H before 2020, perp after; same files as R23). Spent data, so a pass
here is only a direction. ETH-2 (2022–2025) is the only clean set left for a check and is **not** run
without Note's separate OK. BTC C2 and ETH 2026 not loaded.

## Pass (BTC, either cap)

Net > 0 at base and 2× costs, max DD ≤ 10% with no HALT, Sharpe ≥ 0.5, no calendar year worse than −5%,
and at least 3 of the 4 TP variants net > 0 at the same cap.

## BTC result (run once 2026-11-18; `/mnt/project-files/r24-results/`)

2,772 signal bars; regime true 27% of hours. All 8 runs (4 TP variants × 1x/2x cap) **hit the 10% HALT
between 2019-01 and 2019-05**, net −3% to −8% at that point; zero-cost and TP-first runs also negative.
**Fails.**

Diagnostic without HALT (not part of the pass), 2018-04 → 2025, ~65 trades a year, SL ≈ 2.05% away,
notional ≈ 0.62× (cap 1x binds on ~17% of trades, cap 2x almost never):

| Variant | 1x net / DD | 2x net / DD | win rate | 1x zero-cost net |
|---|---|---|---|---|
| TP 1.5R | −43.9% / 49.9% | −47.0% / 53.6% | 39% | −10.6% |
| TP 2R | −45.8% / 54.0% | −49.3% / 57.4% | 32% | −9.9% |
| TP 3R | −42.5% / 58.5% | −47.3% / 62.3% | 23% | −16.6% |
| TP 2R + regime exit | −44.2% / 50.1% | −47.8% / 53.8% | 32% | −8.7% |

Win rates sit just under break-even (1.5R needs 40%, 2R 33%, 3R 25%) before costs, so there is no gross
edge; costs (~0.09R a trade) turn it into a steady loss. Positive only in 2022 and 2024–2025 (TP 3R +15%
and +13% in those years at 1x) and negative in 2019, 2020, 2021 and 2023. 2x ≈ 1x because the 1% risk rule
sets the size, not the cap. ETH-2 not used.
