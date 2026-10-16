# futures-r7: R7 as a daily SHADOW futures book (plan, declared before the code)

Written 2026-10-16. Note chose "ทำทั้งสอง" on 2026-10-16: show the R7 backtest
in the Futures tab (PR #18) **and** run R7 forward as a daily book. R7 passed
development by 0.01 Sharpe and was never confirmed (C1/C2 kept unspent,
`docs/research/r7-futures-longshort-plan.md`). This book is a forward test on
new data. It is simulated money only: no order adapter, nothing live.

## Rules (R7, unchanged)

- Signal at each UTC daily close of the BTCUSDT USDⓈ-M perpetual: LONG if
  close > SMA100 of daily closes, SHORT if below, unchanged if equal or the
  day is invalid. FLAT until SMA100 exists.
- A change of side is filled at the open of the first perp 1h bar starting
  strictly after `decided_at`: close the old position, then open the new one at
  1x (quantity = trading equity at that open / fill price). No rebalancing
  while the side is unchanged.
- Costs: taker 0.05% per side, 2 bps slippage per side.
- Funding: at each daily close the position pays (long) or receives (short)
  `qty × close × that UTC day's summed funding rate`, as in the backtest. Days
  whose funding is still estimated from the premium index are flagged.
- Buy & hold: buy once at the first fill with 0.10% fee and 2 bps slippage on
  the same perp prices, no funding, never halted (the backtest's benchmark).

## Book

- `trading.shadow.trend_perp_book.TrendPerpBook` on the Decimal perp journal
  `ShadowLedger` (balanced entries, duplicate-safe keys).
- Loss limits: the `spot.v1` levels (Note's choice for 1x spot books), applied
  to this 1x book: alert at a 5% day loss or a 15% drawdown, HALT at a 25%
  drawdown. A HALT closes the position at the next 1h open, later decisions
  are recorded as ignored, and only a resume record with a name and reason
  clears it. Nothing writes one automatically.
- Labels `futures-r7/paper` and `futures-r7/buy_and_hold`, state under
  `/mnt/project-files/paper-trading/futures-r7/`.

## Tests (before the book runs)

- The book reproduces the R7 backtest simulator on the same daily data
  (same flips, equity within 1e-6 relative) while no limit trips.
- No fill at or before `decided_at`; a missing 1h bar makes the book wait.
- Replaying a day or a decision changes nothing.
- Funding sign: longs pay positive rates, shorts receive them.
- HALT closes the position and survives save/load; resume needs a name.

## Not in this PR

The daily task and the `trading.*` mirror for the web are owned by the
Phase 5 thread; they are wired there after this merges.
