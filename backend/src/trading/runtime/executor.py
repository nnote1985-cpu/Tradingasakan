"""Order execution inside the worker (testnet-development-plan.md §6.2 steps 7–9, §8.2–§8.4, P4).

The worker calls :meth:`Executor.tick` every 2 s while it holds the writer
lease. One tick:

1. while RECOVERING, checks whether entries may start: every stored order is
   resolved, every open order on the exchange is one of ours, and the
   account check against REST said MATCH within the last 90 s (§8.3 startup order);
2. queries orders that are UNKNOWN (every 2 s for 30 s, then an alert and
   every 30 s; never given up on by time alone) and polls open ones;
3. lets Guardian (P5, ``runtime/guardian.py``) carry out operator HALT/RESUME
   requests, mark the guarded books and, while HALTING, cancel and reduce;
4. asks the conformance profile, when it is on, for its next intent or cancel,
   and the tn-r10a-code-v1 bot (``runtime/r10a.py``) for its daily rebalance;
5. claims outbox rows under the lease token and submits them once, after
   writing SUBMITTING to the store, so a crash mid-submit is reconciled on the
   next start and never sent again.

Every order that adds exposure needs a fresh MATCH, not only the first one:
a buy waits while the last account check is older than 90 s or failed, and a
DISCREPANCY while RUNNING pauses entries until a fresh MATCH comes back. Sells
and cancels of buys are not held by the account check or the normal order caps.

Start-up handles what a stopped writer left: SUBMITTING becomes UNKNOWN and
is reconciled; an intent its outbox row shows as claimed but never sent
ends EXPIRED. Every 10 minutes (and at start) trades are read back from
myTrades from the last stored trade id, so trades made while the worker was
down still reach the ledger (§8.4). Database writes that fail are retried
on the next tick; a stale fencing token stops the worker.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from trading.execution import gateway
from trading.execution.binance_spot import BinanceSpotTestnet, TestnetError, fill_event
from trading.execution.intent import OrderIntent
from trading.execution.limits import Activity, MarketView, PreTradeLimits, check, check_cancel
from trading.execution.orders import Order, OrderState
from trading.execution.rules import TradingRules
from trading.execution.safety import AccountSafety, SafetyState
from trading.execution.store import ExecStore, IntentRefused, StaleFencingToken
from trading.execution.types import Side
from trading.ingest.quotes import Quote
from trading.runtime.alerts import AlertQueue, Severity
from trading.runtime.conformance import BOOK as CONFORMANCE_BOOK
from trading.runtime.conformance import BUDGET as CONFORMANCE_BUDGET
from trading.runtime.conformance import LIMITS as CONFORMANCE_LIMITS
from trading.runtime.conformance import Conformance
from trading.runtime.persist import PersistStore, Record, TradeRecord

if TYPE_CHECKING:
    from trading.runtime.guardian import Guardian
    from trading.runtime.r10a import R10aBot

SYMBOL = "BTCUSDT"
UNKNOWN_FAST_S, UNKNOWN_ALERT_AFTER_S, UNKNOWN_SLOW_S = 2.0, 30.0, 30.0
OPEN_POLL_S = 5.0
RECOVER_EVERY_S = 10.0
BACKFILL_EVERY_S = 600.0
RULES_EVERY_S = 3600.0
INTENT_TTL = timedelta(seconds=60)
REFUSED_RETRY = timedelta(minutes=5)
# The writer lease lasts 15 s and is renewed once per worker tick, so one tick stops starting new network work after this long.
TICK_BUDGET_S = 5.0
# Pre-trade reasons that clear on their own; the intent waits for its TTL instead of being rejected.
TRANSIENT = frozenset({"PRICE_STALE", "RATE_LIMIT", "TOO_MANY_ACTIVE_ORDERS", "RECONCILE_NONE", "RECONCILE_STALE",
                       "RECONCILE_UNAVAILABLE", "RECONCILE_INCONCLUSIVE"})
# The feeds check the account every 30 s; a MATCH older than three rounds no longer vouches for the account.
RECONCILE_MAX_AGE = timedelta(seconds=90)
UNRESOLVED = (OrderState.UNKNOWN, OrderState.RECONCILING)
DAY = timedelta(days=1)


class LeaseFenced(RuntimeError):
    """A store write was refused because this worker's fencing token is no longer current."""


@dataclass
class _Watch:
    due: float = 0.0
    unknown_since: float | None = None
    alerted: bool = False


@dataclass
class Executor:
    store: ExecStore
    adapter: BinanceSpotTestnet
    alerts: AlertQueue
    persist: PersistStore | None = None
    conformance: Conformance | None = None
    guardian: Guardian | None = None
    r10a: R10aBot | None = None
    quote: Callable[[], Quote | None] = lambda: None
    verdict: Callable[[], tuple[str, datetime] | None] = lambda: None     # last account check and when it was read
    limits: PreTradeLimits = field(default_factory=PreTradeLimits)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    mono: Callable[[], float] = time.monotonic
    log: Callable[[str], None] = print
    safety: AccountSafety = field(default_factory=AccountSafety)
    rules: TradingRules | None = None
    orders: dict[str, Order] = field(default_factory=dict)
    waiting: dict[str, tuple[int, OrderIntent]] = field(default_factory=dict)
    records: list[Record] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {"sent": 0, "filled": 0, "canceled": 0, "rejected": 0, "expired": 0,
                                                            "unknown": 0})
    last_error: str | None = None
    paused_by_us: bool = False
    _watch: dict[str, _Watch] = field(default_factory=dict)
    _dirty: dict[str, tuple[Order, str]] = field(default_factory=dict)   # order state still to write, with its reason
    _unfinished_outbox: set[int] = field(default_factory=set)
    _refused: dict[str, datetime] = field(default_factory=dict)
    _due: dict[str, float] = field(default_factory=dict)
    _day: datetime | None = None
    _orders_today: int = 0
    _cancels_today: int = 0
    _last_submit: datetime | None = None
    started: bool = False

    # --- start ------------------------------------------------------------------------

    def start(self, token: int) -> None:
        """Load what a previous writer left and put it back on track (§8.3). Network errors are retried by ``tick``."""
        self.safety = self.store.load_safety()
        now = self.now()
        for o in self.store.unresolved_orders():
            cid = o.client_order_id
            if o.state is OrderState.INTENT:
                if self.store.outbox_status(cid) == "PENDING":
                    continue                                          # claimed and sent by this run later
                o.transition(OrderState.EXPIRED, "claimed by a stopped writer before it was sent")
                self._record(o, token, "start: never sent")
                self.counts["expired"] += 1
                continue
            if o.state is OrderState.SUBMITTING:
                o.transition(OrderState.UNKNOWN, "restart during submit")
                self._record(o, token, "start: restart during submit")
            self._track(o)
        self._roll_day(now)
        self.started = True
        n = len(self.orders)
        self.log(f"executor: {n} order{'s' if n != 1 else ''} to reconcile, safety {self.safety.state.value}")

    def _track(self, o: Order) -> None:
        self.orders[o.client_order_id] = o
        w = self._watch.setdefault(o.client_order_id, _Watch())
        w.due = 0.0
        if o.state in UNRESOLVED and w.unknown_since is None:
            w.unknown_since = self.mono()

    # --- tick ---------------------------------------------------------------------------

    def tick(self, token: int, *, ready: bool) -> None:
        """One pass. ``ready`` is False while signing is paused (clock) or the key failed its check."""
        start = self.mono()

        def in_budget() -> bool:
            return self.mono() - start < TICK_BUDGET_S

        try:
            self._flush_dirty(token)
            if not ready:
                return
            if self.rules is None or self._due_every("rules", RULES_EVERY_S):
                self.rules = self.adapter.get_trading_rules(SYMBOL)
            self._poll_orders(token)
            self._update_pause()
            if self.guardian is not None:
                self.guardian.tick(self, token)                       # never skipped for the tick budget
            if in_budget() and self.safety.state is SafetyState.RECOVERING and self._due_every("recover", RECOVER_EVERY_S):
                self._try_recover()
            if in_budget() and self.conformance is not None and self.safety.allows_entries:
                self._conformance(token)
            if in_budget() and self.r10a is not None:
                self.r10a.step(self, token)
            if in_budget():
                self._submit_claimed(token)
            if in_budget() and self.persist is not None and self._due_every("backfill", BACKFILL_EVERY_S):
                self.backfill_trades()
            self.last_error = None
        except StaleFencingToken as e:
            raise LeaseFenced(str(e)) from e
        except Exception as e:  # noqa: BLE001 - network and database errors are retried next tick; the alert queue holds repeats down
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            self.alerts.push(Severity.WARNING, "executor", f"order handling paused for now: {self.last_error}")

    def _due_every(self, name: str, every: float) -> bool:
        t = self.mono()
        if t >= self._due.get(name, 0.0):
            self._due[name] = t + every
            return True
        return False

    def _roll_day(self, now: datetime) -> None:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if day != self._day:
            self._day = day
            self._orders_today = self.store.submits_since(day)
            self._cancels_today = 0

    # --- persistence ----------------------------------------------------------------------

    def _record(self, o: Order, token: int, reason: str) -> None:
        """Write the order's state; if the write fails it is kept and written again next tick."""
        self._dirty[o.client_order_id] = (o, reason)
        self.store.record_order(o, token, self.now(), reason)
        self._dirty.pop(o.client_order_id, None)

    def _flush_dirty(self, token: int) -> None:
        for o, reason in list(self._dirty.values()):
            self._settle(o, token, reason, count=False)
        self._flush_outbox(token)

    def _settle(self, o: Order, token: int, reason: str, *, count: bool = True) -> None:
        """Record the order; drop it from memory once it is terminal and written."""
        self._record(o, token, reason)
        if o.is_terminal:
            self.orders.pop(o.client_order_id, None)
            self._watch.pop(o.client_order_id, None)
            if count:
                self.counts[{"FILLED": "filled", "CANCELED": "canceled", "REJECTED": "rejected"}.get(o.state.value, "expired")] += 1

    # --- orders -------------------------------------------------------------------------

    def _poll_orders(self, token: int) -> None:
        t = self.mono()
        for cid, o in list(self.orders.items()):
            w = self._watch.setdefault(cid, _Watch())
            if t < w.due:
                continue
            before = (o.state, o.reported_cum_qty, len(o.fills))
            w.due = t + UNKNOWN_FAST_S                                # if the query fails, try again soon
            result = gateway.reconcile(o, self.adapter)
            if result is gateway.ReconcileResult.NOT_FOUND:
                since = w.unknown_since if w.unknown_since is not None else t
                w.unknown_since = since
                if t - since >= UNKNOWN_ALERT_AFTER_S and not w.alerted:
                    w.alerted = True
                    self.alerts.push(Severity.CRITICAL, f"unknown-{cid}", f"order {cid} ({o.intent.side.value} {o.intent.qty}) is still not "
                                     f"found on the Testnet after {int(t - since)} s; new entries stay paused until it is resolved")
                w.due = t + (UNKNOWN_SLOW_S if t - since >= UNKNOWN_ALERT_AFTER_S else UNKNOWN_FAST_S)
            else:
                if w.alerted:
                    self.alerts.push(Severity.INFO, f"unknown-{cid}", f"order {cid} found on the Testnet: {o.state.value}")
                w.unknown_since, w.alerted = None, False
                w.due = t + OPEN_POLL_S
            if (o.state, o.reported_cum_qty, len(o.fills)) != before or o.is_terminal:
                self._settle(o, token, "reconcile")

    def unresolved(self) -> list[Order]:
        return self._unresolved()

    def _unresolved(self) -> list[Order]:
        return [o for o in self.orders.values() if o.state in UNRESOLVED]

    def _entry_block(self) -> str | None:
        """Why the account check does not allow new exposure now, or None after a MATCH read in the last 90 s."""
        v = self.verdict()
        if v is None:
            return "RECONCILE_NONE"
        verdict, at = v
        if verdict != "MATCH":
            return f"RECONCILE_{verdict}"
        if self.now() - at > RECONCILE_MAX_AGE:
            return "RECONCILE_STALE"
        return None

    def _update_pause(self) -> None:
        """Pause entries while an order has been unknown for 30 s or the account check says DISCREPANCY (§8.3).

        The pause lifts once no order is unresolved and a fresh MATCH is in.
        """
        t = self.mono()
        stuck = any(w.unknown_since is not None and t - w.unknown_since >= UNKNOWN_ALERT_AFTER_S for w in self._watch.values())
        block = self._entry_block()
        why = "order outcome unknown" if stuck else "account reconcile discrepancy" if block == "RECONCILE_DISCREPANCY" else None
        now = self.now()
        if why is not None and self.safety.state is SafetyState.RUNNING:
            self.safety.soft_pause(now, why)
            self.store.save_safety(self.safety)
            self.paused_by_us = True
            self.alerts.push(Severity.WARNING, "entries", f"entries paused: {why}")
        elif why is None and block is None and not self._unresolved() and self.paused_by_us and self.safety.state is SafetyState.SOFT_PAUSED:
            self.safety.unpause(now)
            self.store.save_safety(self.safety)
            self.paused_by_us = False
            self.alerts.push(Severity.INFO, "entries", "orders resolved and the account check is MATCH again; entries allowed")

    def _try_recover(self) -> None:
        """RECOVERING -> RUNNING once the stored orders and the exchange agree (§8.3, RT40)."""
        if self.safety.halt_reason is not None:
            return
        if self._unresolved() or self.waiting:
            return
        theirs = [r for r in self.adapter.list_open_orders(SYMBOL) if r.client_order_id not in self.orders]
        if theirs:
            ids = ", ".join(r.client_order_id for r in theirs[:5])
            self.alerts.push(Severity.CRITICAL, "foreign-orders", f"open Testnet orders the bot does not know ({ids}); entries stay "
                             "paused until they are canceled or adopted")
            return
        if self._entry_block() is not None:
            return
        self.safety.recovered(self.now(), reconciled=True)
        self.store.save_safety(self.safety)
        self.alerts.push(Severity.INFO, "entries", "orders reconciled with the Testnet; safety RUNNING, entries allowed")

    # --- conformance ----------------------------------------------------------------------

    def lookup(self, cid: str) -> Order | None:
        """The order for ``cid`` in memory, waiting to be sent, or in the store; None if it was never accepted."""
        return self._lookup(cid)

    def _lookup(self, cid: str) -> Order | None:
        if cid in self.orders:
            return self.orders[cid]
        if cid in self.waiting:
            return Order(self.waiting[cid][1])
        if not self.store.has_intent(cid):
            return None
        return self.store.load_order(cid)

    def _conformance(self, token: int) -> None:
        assert self.conformance is not None
        now = self.now()
        step = self.conformance.step(now=now, rules=self.rules, quote=self.quote(), safety_epoch=self.safety.epoch,
                                     lookup=self._lookup, open_orders=list(self.orders.values()))
        for cid in step.cancels:
            self._cancel(cid, token, CONFORMANCE_LIMITS)
        for intent, unit_price in step.intents:
            cid = intent.client_order_id
            if intent.increases_exposure and self._entry_block() is not None:
                continue                                              # asked again next tick
            if (at := self._refused.get(cid)) is not None and now - at < REFUSED_RETRY:
                continue
            try:
                self.store.accept_intent(intent, unit_price=unit_price, budget=CONFORMANCE_BUDGET)
            except IntentRefused as e:
                self._refused[cid] = now
                self.alerts.push(Severity.WARNING, f"refused-{intent.leg}", f"conformance {intent.leg} not accepted: {e}")

    def cancel(self, cid: str, token: int, *, emergency: bool = False) -> None:
        """Cancel an open order; ``emergency`` (Guardian while HALTING) only stops at the emergency ceiling, whatever the side."""
        o = self.orders.get(cid)
        if o is not None:
            self._cancel(cid, token, self._limits_for(o.intent), emergency=emergency)

    def _cancel(self, cid: str, token: int, limits: PreTradeLimits, *, emergency: bool = False) -> None:
        o = self.orders.get(cid)
        if o is None or o.is_terminal:
            return
        if held := check_cancel(self._activity(), limits, reduces_risk=emergency or o.intent.side is Side.BUY):
            self.alerts.push(Severity.WARNING, "cancel-limit", f"{held[0]}: {cid} stays open")
            return
        self._cancels_today += 1
        gateway.cancel(o, self.adapter)
        self._settle(o, token, "cancel")

    # --- submit -------------------------------------------------------------------------

    def _activity(self) -> Activity:
        active = sum(1 for o in self.orders.values() if o.is_open)
        return Activity(active, self._orders_today, self._cancels_today, self._last_submit)

    def _limits_for(self, intent: OrderIntent) -> PreTradeLimits:
        return CONFORMANCE_LIMITS if intent.book == CONFORMANCE_BOOK else self.limits

    def _submit_claimed(self, token: int) -> None:
        now = self.now()
        self._roll_day(now)
        for oid, intent in self.store.claim_outbox(token, now):
            self.waiting[intent.client_order_id] = (oid, intent)
        for cid, (oid, intent) in list(self.waiting.items()):
            order = Order(intent)
            if now - intent.created_at > INTENT_TTL:
                order.transition(OrderState.EXPIRED, "TTL passed before it could be sent")
                self._finish(order, oid, token, "ttl")
                continue
            reasons = self._pretrade(intent, now)
            if reasons and set(reasons) <= TRANSIENT:
                continue
            if reasons:
                order.transition(OrderState.REJECTED, "pre-trade: " + ",".join(reasons))
                self._finish(order, oid, token, "pre-trade")
                self.alerts.push(Severity.WARNING, f"pretrade-{cid}", f"{intent.book} {intent.leg} refused before sending: {', '.join(reasons)}")
                continue
            self._send(order, oid, token)

    def _pretrade(self, intent: OrderIntent, now: datetime) -> list[str]:
        if self.rules is None:
            return ["PRICE_STALE"]
        q = self.quote()
        if q is None:
            return ["PRICE_STALE"]
        mark = q.ask if intent.side is Side.BUY else q.bid
        seen = datetime.fromtimestamp(q.received_ms / 1000, UTC)
        out = check(intent, now=now, rules=self.rules, market=MarketView(mark, min(seen, now)), activity=self._activity(),
                    safety=self.safety, limits=self._limits_for(intent))
        if intent.increases_exposure and (block := self._entry_block()) is not None:
            out.append(block)
        return out

    def _send(self, order: Order, oid: int, token: int) -> None:
        cid = order.client_order_id
        # SUBMITTING is written before the network call; if that write fails nothing is sent and the intent waits.
        gateway.submit(order, self.adapter, before_send=lambda o: self.store.record_order(o, token, self.now(), "submit"))
        # From here the order may exist on the exchange: it is tracked whatever happens to the writes below.
        self.waiting.pop(cid, None)
        self._orders_today += 1
        self._last_submit = self.now()
        self.counts["sent"] += 1
        if order.state is OrderState.UNKNOWN:
            self.counts["unknown"] += 1
        if not order.is_terminal:
            self._track(order)
        self._unfinished_outbox.add(oid)
        if order.state in (OrderState.PARTIALLY_FILLED, OrderState.FILLED):
            try:
                gateway.apply_fills(order, self.adapter)
            except TestnetError:
                pass                                                   # the next poll pulls them
        try:
            self._settle(order, token, "submit answer")
        finally:
            self._flush_outbox(token)

    def _flush_outbox(self, token: int) -> None:
        for oid in list(self._unfinished_outbox):
            self.store.finish_outbox(oid, token, self.now())
            self._unfinished_outbox.discard(oid)

    def _finish(self, order: Order, oid: int, token: int, reason: str) -> None:
        self.waiting.pop(order.client_order_id, None)
        self._unfinished_outbox.add(oid)
        self._settle(order, token, reason)
        self._flush_outbox(token)

    # --- trade backfill (§8.4) ------------------------------------------------------------

    def backfill_trades(self) -> int:
        """Queue trades from myTrades newer than the last stored one for the open epoch. Returns how many were read."""
        assert self.persist is not None
        epoch = self.persist.open_epoch()
        if epoch is None:
            return 0
        last_id, opened_at = self.persist.trade_cursor(epoch, SYMBOL)
        now_ms = int(self.now().timestamp() * 1000)
        rows: list[dict[str, Any]] = []
        if last_id is None:                                   # nothing stored yet: walk the epoch in 24 h windows
            start = int(opened_at.timestamp() * 1000)
            while start <= now_ms and not rows:
                rows = self.adapter.my_trades(SYMBOL, start_ms=start, end_ms=start + int(DAY.total_seconds() * 1000) - 1)
                start += int(DAY.total_seconds() * 1000)
            last_id = int(rows[-1]["id"]) if rows else None
        while last_id is not None:
            page = self.adapter.my_trades(SYMBOL, from_id=last_id + 1)
            rows += page
            if len(page) < 1000:
                break
            last_id = int(page[-1]["id"])
        for t in rows:
            cid = self.store.client_id_for(str(t["orderId"])) or ""
            self.records.append(("trade", TradeRecord(fill_event(SYMBOL, t, cid), now_ms)))
        return len(rows)

    def drain_records(self) -> list[Record]:
        out, self.records = self.records, []
        return out

    # --- status ---------------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        t = self.mono()
        return {
            "safety": self.safety.state.value, "open_orders": sum(1 for o in self.orders.values() if o.is_open),
            "unresolved": [{"client_order_id": o.client_order_id, "state": o.state.value,
                            "for_s": int(t - (self._watch[o.client_order_id].unknown_since or t))
                            if o.client_order_id in self._watch else 0} for o in self._unresolved()],
            "waiting": len(self.waiting), "orders_today": self._orders_today, "cancels_today": self._cancels_today,
            "counts": dict(self.counts), "last_error": self.last_error,
            "conformance": None if self.conformance is None else self.conformance.body(),
            "guardian": None if self.guardian is None else self.guardian.body(),
            "r10a": None if self.r10a is None else self.r10a.body(),
        }
