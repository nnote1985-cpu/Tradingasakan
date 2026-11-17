# R18: SL/TP and risk sizing on R10a's long side, on the perp (pre-declared)

Written 2026-11-17, before any R18 backtest was run. Note asked how SL/TP would be set, worried that
a no-TP rule may look better in backtest than live, wants a strategy that holds up in every market,
and noted that decisions differ with leverage. R18 adds exchange-side SL/TP to R10a's long signal on
the BTCUSDT perp and runs it at fixed 1x, fixed 2x and risk-based sizing. Long only (R15–R17 showed
no short edge). It is a grid, so a pass is a candidate only.

## Signal (unchanged R10a band, daily)

Band state on daily SMA100: in above SMA × 1.02, out below SMA × 0.98 (R10a's funding half-size is
dropped here; sizing is set by the rules below). Decided at the UTC day close, filled at the next open.

- **Enter long** when flat and the band state is in, and either the state turned in today (fresh
  signal) or the close is above the highest close of the previous 20 days (re-entry after a SL/TP
  exit while the trend still holds).
- **Exit** at the next open when the band state turns out.

## Exits on the exchange (every trade; intrabar on 1h high/low, stop first if both touched)

R = SL distance = 3 × ATR14 (daily, Wilder, at the signal day). Five exit variants:
- **E0 none:** no SL, no TP (R10a on the perp; reference).
- **E1 SL + trailing, no TP:** SL at entry − R; it trails at highest 1h high since entry − R, moving up only.
- **E2 SL + TP 2R** (fixed).
- **E3 SL + TP 3R** (fixed).
- **E4 SL + half at 2R, rest trailing** (as E1).

## Sizing (three)

- **1x:** notional = 1 × equity at entry. **2x:** notional = 2 × equity (cross margin, the whole account).
- **Risk 2%:** notional = 2% of equity ÷ (R / entry), capped at 2× equity.
E0 runs only with 1x and 2x. 14 runs per window.

Liquidation: cross margin on the whole account, maintenance 0.5% of notional; if a 1h low reaches the
liquidation price, the book is liquidated and stops. Liquidation distance vs SL distance is reported.

## Costs

Perp taker 0.05% + 2 bps per side (TP fills also as taker, conservative); funding at the real
00/08/16 UTC settlements (long pays positive rates). 10,000 USDT. Costs ×2 also reported.

## Windows

dev 2022-05-01 → 2026-01-01 and C1 2020-04-15 → 2022-01-01 (once, after dev is committed, all runs).
C2 not used.

## Market regimes (fixed now)

Each calendar part-year is labelled from BTC buy & hold in that period: **bull** if > +20%, **bear** if
< −20%, otherwise **sideways**. Expected: 2022 (May–Dec) bear, 2023 and 2024 bull, 2025 to be seen;
2020 (Apr–Dec) and 2021 bull. Per regime year: net, max DD, vs B&H and R10a.

## Pass (per run, both windows)

Same as R15: A Sharpe > R10a's (spot) and max DD ≤ R10a's + 5 points; B net > 0 and (Sharpe > B&H's
or max DD ≤ ½ B&H's); C B holds with costs ×2; no liquidation. Plus **D (every market):** in each
bear or sideways year, loss no larger than R10a's in that year; in each bull year, net ≥ half of B&H's.

## Dev result (run once 2026-11-17; `/mnt/project-files/r18-results/dev/`)

Regimes: 2022 May–Dec bear, 2023 bull, 2024 bull, 2025 sideways. R10a spot +245% / 1.00 / 26%;
B&H +133% / 0.46 / 60%. Best SL/TP run by Sharpe: E3 TP 3R with 2% risk, +22% / 0.81 / 6% DD. At 1x:
E0 none +177% / 0.78 / 31%; E2 TP 2R +102% / 0.56; E3 TP 3R +92% / 0.54; E1 trail +24% / 0.21; E4
+24% / 0.22. No run passes A (Sharpe > R10a). No liquidation. C1 next, unchanged.
