# Forward paper book "3a" (pathA-r10a-hedge): plan and acceptance, declared before the code

Written 2026-11-17, before any book code. On 2026-11-17 Note approved ("โอเค") opening a paper book
for option 3a of the futures comparison: hold BTC on spot for the long side, and when R10a is out,
keep the spot and short an equal amount of perp (delta neutral) to collect funding instead of selling.
It is R20 at size 1.0 (R10a layered on the R19 carry base, `docs/research/r20-layer1-r10a-plan.md`,
PR #97) and the unfiltered form of R15's F3. Backtest (R20 s = 1.0, run once, not re-run here):

| window | net (per year) | Sharpe | max DD | R10a alone (Sharpe / DD) |
| --- | --- | --- | --- | --- |
| dev 2022-05 → 2025 | +257% (41%) | 1.03 | 25.5% | 1.00 / 26% |
| C1 2020-04 → 2021 | +488% (181%) | 2.02 | 22.8% | 1.93 / 23% |

C2 (2026-01 → 10) is not used. The gain over R10a is small; the point of the book is to measure it
forward next to `pathA-r10a`, which sees the same signal.

## Rules (the research rules; the only additions are the perp stop and the wallet split, below)

- **Signal.** R10a's weight w ∈ {0, 0.5, 1}, computed exactly as `scripts/paper_trade_r10a.py`
  does: ±2% band latch on SMA100 of daily perp closes, times path A's funding factor (0.5 when funding
  is hot). Decided at each daily close, by the daily job.
- **Targets** (fractions of total equity E): spot BTC = w + 0.5(1 − w); perp = −0.5(1 − w).
  w = 1: 100% spot, no short. w = 0: 50% spot and a 50% short, net exposure 0. w = 0.5: 75% spot,
  25% short, net 50%. Net exposure always equals R10a's.
- **Trading** (R15 `simulate`): every daily run sets a check at the first 1h bar after the decision;
  at that bar, E is taken at the 1h opens (spot and perp), then each leg (perp first, then spot)
  trades to its target when its target changed or it drifts more than max(20% of target, 5% of E).
  Spot fills at the spot 1h open, perp at the perp 1h open, each ± 2 bps slippage. Fees: spot 0.10%,
  perp 0.05% (taker).
- **Marks and funding.** Spot marked at the spot daily close, perp at the perp daily close. Funding:
  perp quantity × perp close × the day's summed funding rate (shorts receive positive rates), booked
  at the close; days that used an estimate are flagged, as in futures-r7.
- **Wallets.** Spot and perp are two Decimal ledgers (`SpotLedger`, `ShadowLedger`) linked by
  transfer entries. At each fill the spot wallet keeps exactly what its buy needs and the rest sits
  in the perp wallet as margin (≈ 1x). A transfer out of the perp wallet is capped at its cash
  (unrealized profit is not moved); if the cap binds, the spot buy is smaller and the drift rule
  catches up later. This is the only way the book can differ from the single-cash backtest.
- **Perp stop (R17 / R18 rule, not in the backtest).** Whenever a perp fill leaves a short open, a
  stop-market buy (reduce only, resting on the exchange) is set at fill price + 3 × ATR14 (daily perp,
  Wilder, at the decision close). It is simulated on 1h highs from the fill on: filled at the stop, or at
  the bar's open if it gapped through, + 2 bps. After a stop the book is net long until the next daily
  check, which re-hedges (the short target is unchanged) with a new stop.
- **Liquidation distance** (1x isolated, maintenance 0.5%) is recorded each day next to the stop.
- **Loss limits.** `spot.v1` on the combined equity, the same as pathA-r10a so the two compare:
  alert at a 5% day loss or 15% drawdown, HALT at 25% drawdown (close the short and sell all spot at
  the next 1h open, then wait for a named resume record). R17's 3% daily cap ("no new entry that day")
  is not used: the only entries here are hedges, which lower risk.
- **Benchmark.** Buy & hold on spot from the first fill, 0.10% fee, as pathA-r10a.

## Code

- `trading.shadow.hedge_book.HedgeBook` (format `hedge_book.v1`, rules version `pathA-r10a-hedge`).
- `ShadowLedger.transfer`: one balanced entry (cash against `transfers`) per wallet side.
- `scripts/paper_trade_r10a_hedge.py`; state under `/mnt/project-files/paper-trading/pathA-r10a-hedge/`.
- `scripts/paper_run.py --book pathA-r10a-hedge`; `scripts/trading_sync_sql.py` copies both ledgers
  into one portfolio `pathA-r10a-hedge/paper` (transfers net to zero) plus its buy & hold.

## Acceptance tests

1. **Same as the backtest.** On synthetic bars (1h open = daily open, stop off, loose limits) the
   book's daily equity equals a reference implementation of R15 `simulate` with R20 s = 1.0 targets
   within 1e-6 relative, with the same trades on the same days.
2. **Stop.** A 1h high through the stop closes the whole short at the stop (or the gap open) + slippage;
   the next check re-hedges with a new stop.
3. **Funding.** A short receives positive funding; the amount is qty × close × rate.
4. **Wallets.** Every entry of both ledgers balances, transfers sum to zero across the two wallets,
   and no wallet's cash goes below zero.
5. **HALT.** A 25% drawdown closes both legs at the next open; later decisions are ignored until a
   resume record.
6. **State safety.** The state loads only into this book, and save/load round-trips.
7. The existing tests still pass.

## After merge

Create `frozen/pathA-r10a-hedge` at the merge commit, run it once for the opening decision, and add
it to the daily job beside pathA-r10a (and to the weekly report). Nothing live, no exchange orders.

## Results (added after the code; no rule above was changed)

- `tests/unit/test_hedge_book.py`, 6 tests, and one sync test in `tests/unit/test_sync_formats.py`,
  all pass. On two random markets (240 days, weights switching between 0, 0.5 and 1) the book's daily
  equity equals the R15 reference to 1.3e-12 relative, trade for trade, once the reference's spot buy
  is capped at the cash it has. R15 itself lets cash dip below zero by the fee of a buy to 100%; against
  R15 exactly the book is within 1.3e-4 (test bound 1e-3). That is the only difference found.
- Full suite: 703 tests pass; ruff clean; mypy clean on the package.
- Smoke run against live data with throwaway state (2026-11-17): opens on the 2026-11-16 close with
  R10a out (close 64,856 below SMA100 × 0.98 = 76,199), so 50% spot and a 50% short, stop at the fill
  + 3 × ATR14 (ATR14 2,683.82). A replay on 2026-11-02 → 16 data (not a forward record) ended at
  +0.03% while buy & hold fell 17%, with 13 USDT funding received (proxy rates) and no ledger problems.
