# Phase 3 (Research MVP): scope and acceptance, declared before the code

Written 2026-10-15, before any Phase 3 code. BUILDPLAN v4.2 §24: deliverables
"exact v1, Shadow ledger, risk/halts, basic UI"; gate "costs/fill timing/equity/
actual-policy halt ถูกต้อง". Rules come from §6, §9, §10 (BAR_RESEARCH), §11
and §22.3, with the fixtures of §25.1.

## What already exists (Phase 0.5) and what is missing

`strategies/trend_breakout_v1.py`, `risk/sizing.py` and
`simulator/bar_backtest.py` already implement v1's signal (T01–T05), sizing
(T06–T07) and a float bar simulator (T13–T15). The simulator is a research
tool: float cash, no journal, no duplicate protection, no economic equity, a
daily breach that resets every day, no durable state, and entries at the first
bar opening at the signal close with no decision time.

## Deliverables

1. **Shadow ledger** (`trading.shadow.ledger`): Decimal, append-only journal;
   every fill, fee, funding payment and operating cost is one balanced entry
   keyed by its source event, so a repeated event changes nothing (T16).
   Position, average entry and cash are projections of the journal. Partial
   closes realise only the closed quantity; fees never go into the average
   entry. `trading_equity = cash + unrealised at mark`;
   `economic_equity = trading_equity − operating costs` (§11.1). Slippage is a
   metric, never a second deduction (T15).
2. **Risk state** (`trading.shadow.risk_state`): UTC day start for both
   equities, high-water mark, and a HALT latch with reason and time, saved
   with the book. HALT triggers on daily loss ≥ 2% of either day-start equity,
   drawdown ≥ 8% of the high-water mark, or economic equity ≤ 0 (ruin).
   Midnight and restart never clear it; only a separate resume record could,
   and this PR adds none (`automatic_resume_after_halt: false`, T22).
   Remaining day budget subtracts the open position's loss-to-stop from the
   current mark (§9.5).
3. **Shadow portfolio** (`trading.shadow.portfolio`): v1 exactly as §6:
   candidates from `trend_breakout_v1`, sizing from `plan_entry`, stop 2×ATR,
   TP 2× the actual initial price risk from the fill, 24h time exit, 4h EMA200
   trend exit, 2h cooldown from the exit fill, no reversal.
   Fill timing (§10.2, BAR_RESEARCH): each decision records `decision_at`;
   `eligible_fill_at = decision_at + validation_delay + network_delay`
   (DEFAULT 0s + 100ms); the entry fills at the open of the first execution
   bar whose open is at or after `eligible_fill_at`, never earlier. Same-bar
   SL/TP is conservative with an ambiguity label (T13); a gap through the stop
   fills at the open (T14). Funding is charged on the position held at each
   funding print. On HALT the position is flattened at the next bar, no new
   entries open, and equity and operating costs keep accruing to the endpoint
   (`ABSORBING_UNTIL_ENDPOINT`, T31, T38).
   The book state saves and loads between runs; a loaded book continues
   exactly as an unbroken run would.
4. **Basic UI**: `/api/v1/overview` lists portfolios from the `trading` schema
   with mode, currency, latest trading and economic equity, high-water mark and
   the time of the latest equity snapshot; the web Overview shows them with the
   SHADOW badge and a stale label. Read-only.

## Acceptance tests

- T15 worked example: economic net +78.0886 USDT, slippage not deducted twice.
- T16: the same execution or funding event twice leaves cash and quantity
  unchanged.
- Partial close realises P&L on the closed quantity only; journal entries all
  balance.
- T38: halted at 9,180 with 0.20/day for 20 days gives economic 9,176.
- T22: a halted book saved and loaded, then advanced past midnight, is still
  halted.
- T31: a breach mid-run flattens at the next bar, blocks later candidates, and
  the equity series runs to the endpoint.
- Fill timing: an entry never fills before `eligible_fill_at`; with 1m bars a
  decision 1s after the 10:00 close fills at the 10:01 open, not 10:00.
- Exact v1: on a synthetic series the Shadow portfolio with zero decision
  delay produces the same trades (entry/exit times, reasons, quantities) as
  `bar_backtest`, and P&L within rounding.
- Daily loss is measured on both trading and economic equity; drawdown on
  economic equity.
- Save/load: running a series in two halves equals running it whole.

## Not in this PR

No forward v1 book is started: choosing to run v1 forward is a research
decision for Note. No QUOTE_FORWARD simulator (no quote data reachable), no AI
arm, no Supabase migration or writes, no deploy, no order adapter.
