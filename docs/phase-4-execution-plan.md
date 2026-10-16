# Phase 4 (Execution MVP): scope and acceptance, declared before the code

Written 2026-10-16, before any Phase 4 code. Note chose to start Phase 4 on
2026-10-16 ("งั้นเริ่ม phase 4 เลย"). BUILDPLAN v4.2 §24: deliverables
"adapter, reservations/outbox, protection, Guardian"; gate "ต้องผ่านก่อนส่ง
testnet orders ต่อเนื่อง/Live". Rules come from §12–§15, §23 and the DoD in
§26.2.

## Boundaries

- **TESTNET only.** `LIVE_SUPPORTED_IN_BUILD` stays `False`; nothing in this
  phase flips it. The adapter refuses any mainnet host: base URLs are fixed
  constants for the testnet, not settings.
- **Runs on Note's PC.** The cloud gets HTTP 451 from Binance REST, so the
  gateway and Guardian run on Note's Windows PC (or a VPS later). CI and the
  cloud run only the offline tests (fake exchange, recorded fixtures).
- **Keys stay on the PC.** Testnet API keys are read from environment
  variables (`BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET`) on the
  PC only; never in the repo, Supabase, logs or fixtures.
- **No effect on research.** Testnet fills are not performance evidence
  (§23: testnet prices differ from mainnet). The frozen paper books
  (pathA-v1.1, v1.2, p5-r1/r2, futures-r7), the daily task and the sync
  scripts are not touched.
- **Connector:** ccxt (MIT) behind our own `ExchangeAdapter` interface (§12.1),
  so the core never builds endpoints. Nautilus Trader (LGPL, v2 RC) and
  freqtrade (GPL) are sources of ideas only; small vnpy (MIT) pieces may be
  reused with a notice in `docs/third_party/`.

## Order of products

1. **Binance Spot Testnet, BTCUSDT, long/flat, 1x** first. The running books
   are spot, and spot has no liquidation, so it is the safest place to prove
   the order lifecycle.
2. **Binance USDⓈ-M Futures Testnet** second, because R7 is a 1x perp book
   and needs native stop coverage (§13.2), reduce-only and funding.

## Deliverables (one PR each, in order)

1. **Order core, no network** (`trading.execution`):
   - `Instrument` from exchange rules (tick size, step size, min qty,
     min notional); every price and quantity is rounded through it, in
     Decimal.
   - `OrderIntent` with a stable `client_order_id` derived from the business
     key (book, decision, leg), so a retry can never mint a new id (§12.3).
   - Order state machine exactly as §13.1 (INTENT … RECONCILING), position and
     account-safety states as §13.2 (RUNNING, SOFT_PAUSED, REDUCING, HALTING,
     HALTED, RECOVERING). Illegal transitions raise.
   - Pre-trade checks (vnpy RiskManager style), testnet DEFAULTs:
     max notional per order 2,000 USDT; max 1 order per second; max 2 active
     orders; max 20 orders per UTC day; max 50 cancels per day; price
     freshness 60 s; reject if the limit price is more than 1% from the latest
     mark; quantity must pass LOT_SIZE and MIN_NOTIONAL.
   - Risk reservation: reserved risk turns into position risk on fill and is
     released only for quantity confirmed unfilled (§12.2).
   - `FakeExchange` for tests: acknowledgements, partial fills, timeouts that
     did or did not reach the exchange, duplicate and out-of-order events.
2. **Durable store**: a migration adding `trading.exec_*` tables (intents,
   outbox, order events, fills keyed by execution id, safety state with epoch
   and HALT latch, writer lease), tested on local PostgreSQL like Phase 1.
   Intent, reservation and outbox row commit in one transaction (§12.2).
   Plus a local append-only emergency journal (JSONL on the PC) for when the
   database is unreachable, synced exactly once afterwards.
3. **Binance Spot Testnet adapter** (ccxt, sandbox mode): the §12.1 methods.
   A `scripts/exec_capabilities.py` run on Note's PC writes the capability
   matrix from the actual testnet account (§26.2: not docs only) and records
   request/response fixtures with secrets removed; offline tests replay them.
4. **Gateway, reconciliation and Guardian**:
   - Gateway claims outbox rows under the writer lease and fencing epoch,
     rechecks epoch, TTL, health and reservation, submits with the same
     client order id, and stores the response or UNKNOWN. UNKNOWN is resolved
     by query (client id, recent orders, fills, balances), never by a blind
     retry.
   - Startup always enters RECOVERING (§15.1); a mismatch between our ledger
     and the exchange (balance, open orders, fills) latches HALT for Note.
   - Guardian, no LLM: heartbeat; stale market data or private stream blocks
     new entries; a REDUCING state with a cooldown sits between the spot.v1
     alert and HALT (stop-loss streak or a fast drawdown); daily loss and
     drawdown use the spot.v1 levels.
   - Soft pause, emergency hard kill (cancel entry remainders only, never a
     blanket cancel-all, flatten, reconcile until flat is confirmed, §14.4)
     and manual resume only. No auto-resume at midnight.
5. **Drills and runbook** on the testnet, with logs kept: order timeout,
   restart with an order in flight, database outage, host restart, hard kill,
   late fill after HALT. `docs/runbook-execution.md` says what to do for each
   incident in §15.2. A read-only web panel shows execution state with a
   TESTNET badge.
6. **Futures testnet adapter**: one-way mode, 1x, reduce-only exits, native
   stop coverage confirmed active with the right side and quantity, partial
   fill protection (the 0.069/0.040 example in §13.2), funding events.

## What drives testnet orders

A scenario runner sends pre-written order scenarios (buy, partial, cancel,
rebalance to a target weight). After deliverable 4 passes, an optional mirror
may follow pathA-v1.2's target weight on the spot testnet, labelled TESTNET,
to exercise the daily path; it never feeds the paper books or any comparison.

## Acceptance (BUILDPLAN §26.2)

- Capability matrix checked against the actual testnet account.
- Intent/outbox/reservation/dedupe/reconciliation pass partial, unknown and
  race tests, including: a timeout that did reach the exchange produces one
  journal entry, not two; a duplicate fill event changes nothing; cumulative
  filled quantity never goes down on a stale event; CANCELED with a partial
  fill still leaves an OPEN position.
- Hard kill confirmed against the real testnet state (flat, no entry orders,
  no UNKNOWN intents), not our own ledger.
- Writer lease and fencing: a second gateway cannot submit; an intent from an
  old epoch is refused; a late fill after HALT is reduced.
- Restart, database outage, host outage and alert drills done on the testnet,
  with a runbook.
- Secrets, RBAC/RLS and environment isolation reviewed: no key in the repo,
  database, logs or fixtures; mainnet hosts refused; LIVE still refused.

## Not in this phase

Live trading, mainnet keys, leverage above 1x, options, and any change to how
the paper books are scored.
