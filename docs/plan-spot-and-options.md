# Plan: spot first, buy-only options later

Written 2026-10-15 at Note's request ("เริ่มพัฒนาและวางแผนสำหรับ spot และรองรับการเพิ่ม
buy option ในอนาคต"). This is a plan; nothing here enables live trading.

## Where things stand

| Piece | Spot today | Options today |
| --- | --- | --- |
| Instrument contract (`contracts/instruments.py`) | `SPOT` enabled, average cost basis | `OPTION` defined, **disabled** |
| Data (Phase 2 collector) | BTCUSDT spot 1h collected daily | none |
| Paper book pathA-v1.1 | spot book (float ledger), fills on spot prices | – |
| Shadow engine (Phase 3) | perp-style ledger (P&L, funding, shorts) | – |
| Research | R3–R6 simulated as spot books | – |

## Spot track

1. **Spot ledger in the Shadow engine.** Add a spot account model to the Decimal
   journal: USDT cash and BTC held as separate balances, buys limited by cash,
   no short sales, no funding, fees in the quote asset (fee in BNB or base asset
   recorded as its own line when a venue charges that way), realised P&L by
   average cost (§11.3, `COST_BASIS[SPOT]`). Same duplicate-event protection and
   tests as the perp ledger (T15/T16 equivalents).
2. **Instrument-aware portfolio.** The Shadow portfolio takes an `Instrument`;
   for `SPOT` it refuses SHORT candidates, sizes notional ≤ cash, and skips
   funding. Exits and HALT rules are unchanged.
3. **Move pathA onto it as a new book version**, not by changing pathA-v1.1:
   run both side by side and check they agree before retiring the float
   ledger (pre-declared, same as the v1 → v1.1 change).
4. **Spot risk profile.** The 2%/8% halt profile was written for leveraged
   futures. A spot profile needs its own limits, set with Note before any
   spot book runs forward; until then spot research reports breaches
   (`record_only`) instead of halting.

## Buy-only options track (later)

Only buying calls or puts: the most you can lose is the premium and fees, so
there is no margin or liquidation. Selling options is out of scope.

1. **Contract.** Option instrument with underlying, expiry, strike, right
   (call/put), multiplier and settlement (Binance European options settle in
   USDT at expiry against the index). Risk per trade = premium + fees, checked
   by the Risk Engine like a stop distance.
2. **Data (DISCOVERY).** Checked 2026-10-15 on data.binance.vision: hourly
   option summaries with bid/ask, mark price and IV (`EOHSummary`) exist only
   for part of 2023 (2023-06-01 and 2023-10-20 present; 2023-01-02, 2024-06-01,
   2025-06-01 and 2026-09-01 missing). The BTC volatility index (`BVOLIndex`)
   runs through 2026. So there is not enough option-quote history for a real
   backtest; the choices are (a) forward collection of quotes once a reachable
   source exists, (b) another venue's history (Deribit; data rights to check),
   or (c) Black-76 pricing from the volatility index as a labelled
   approximation.
3. **Simulator.** Fill at the ask (buy) and mark at bid or mark price, never at
   theoretical value when quotes exist; expiry settles against the index;
   options with no quote are UNKNOWN, not zero.
4. **First hypotheses to test once data exists** (each pre-declared):
   a protective put on a spot holding to cap drawdown; a call instead of spot
   during a pathA "risk-on" signal, which caps the loss at the premium.
5. **Gate.** `OPTION` stays out of `ENABLED_INSTRUMENT_TYPES` until steps 1–3
   pass their tests.

## Order of work

Spot ledger → instrument-aware portfolio → spot risk profile with Note →
pathA on the new engine (new version) → options contract and data discovery →
options simulator → option hypotheses.
