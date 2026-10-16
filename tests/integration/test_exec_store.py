"""Phase 4 deliverable 2: execution store on a throwaway local PostgreSQL (docs/phase-4-execution-plan.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.contracts.enums import TradingMode
from trading.execution import gateway
from trading.execution.fake_exchange import Behaviour, FakeExchange
from trading.execution.intent import OrderIntent
from trading.execution.orders import Order, OrderState
from trading.execution.rules import TradingRules
from trading.execution.safety import AccountSafety, SafetyState
from trading.execution.store import ExecStore, IntentRefused, StaleFencingToken
from trading.execution.types import Side

psycopg = pytest.importorskip("psycopg")

T0 = datetime(2026, 10, 16, 12, tzinfo=UTC)
TTL = timedelta(seconds=30)
RULES = TradingRules("BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"), min_qty=D("0.00001"),
                     max_qty=D("9000"), min_notional=D("5"))


BOOK = {"name": "testnet/spot"}


def intent(leg: str, qty: str = "0.03", side: Side = Side.BUY, epoch: int = 1) -> OrderIntent:
    return OrderIntent.build(RULES, side=side, qty=D(qty), mode=TradingMode.TESTNET, book=BOOK["name"],
                             decision_id="d1", leg=leg, created_at=T0, safety_epoch=epoch)


@pytest.fixture
def store(db):  # noqa: F811
    account = f"acct-{datetime.now(UTC).timestamp()}"
    BOOK["name"] = f"testnet/{account}"                          # client ids are global, so each test gets its own book
    s = ExecStore(db, account)
    safety = AccountSafety()
    safety.recovered(T0, reconciled=True)
    s.save_safety(safety)
    return s


def test_intents_must_be_testnet_at_the_database_too(db):  # noqa: F811
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute("insert into trading.exec_intents (client_order_id, account, mode, book, decision_id, leg, symbol, side, "
                   "order_type, qty, safety_epoch, created_at) values ('x', 'a', 'LIVE', 'b', 'd', 'l', 'BTCUSDT', 'BUY', "
                   "'MARKET', 1, 1, now())")


def test_accept_is_atomic_and_idempotent(store):
    i = intent("a")
    assert store.accept_intent(i, unit_price=D("65000"), budget=D("5000")) is True
    assert store.accept_intent(i, unit_price=D("65000"), budget=D("5000")) is False
    assert store.reserved() == D("1950")
    with pytest.raises(IntentRefused, match="INSUFFICIENT_BUDGET"):
        store.accept_intent(intent("b", qty="0.05"), unit_price=D("65000"), budget=D("5000"))
    n = store.conn.execute("select count(*) from trading.exec_outbox o join trading.exec_intents i using (client_order_id) "
                           "where i.account = %s", (store.account,)).fetchone()[0]
    assert n == 1                                                  # the refused intent left nothing behind


def test_entries_refused_while_halted_and_old_epoch_refused(store):
    s = store.load_safety()
    assert s.state is SafetyState.RECOVERING                      # a reload always starts RECOVERING
    s.recovered(T0, reconciled=True)
    s.halt(T0, "drawdown")
    store.save_safety(s)
    with pytest.raises(IntentRefused, match="STALE_SAFETY_EPOCH"):
        store.accept_intent(intent("a"), unit_price=D("65000"), budget=D("5000"))
    with pytest.raises(IntentRefused, match="ENTRIES_BLOCKED_HALTING"):
        store.accept_intent(intent("b", epoch=2), unit_price=D("65000"), budget=D("5000"))
    assert store.accept_intent(intent("c", side=Side.SELL, epoch=2), unit_price=D("65000"), budget=D("5000"))
    reloaded = store.load_safety()
    assert reloaded.state is SafetyState.HALTING and reloaded.halt_reason == "drawdown" and reloaded.epoch == 2


def test_lease_fencing(store):
    a = store.acquire_lease("gw-a", T0, TTL)
    assert a is not None and a.token == 1
    assert store.acquire_lease("gw-b", T0 + timedelta(seconds=5), TTL) is None
    b = store.acquire_lease("gw-b", T0 + timedelta(seconds=31), TTL)
    assert b is not None and b.token == 2
    store.accept_intent(intent("a"), unit_price=D("65000"), budget=D("5000"))
    with pytest.raises(StaleFencingToken):
        store.claim_outbox(a.token, T0 + timedelta(seconds=32))
    assert len(store.claim_outbox(b.token, T0 + timedelta(seconds=32))) == 1


def test_full_cycle_with_timeout_and_restart(store):
    """Accept, claim, timeout that reached the exchange, crash, recover, reconcile once."""
    ex = FakeExchange(RULES, mark=D("65000"), partial_qty=D("0.01"), repeat_fills=True)
    ex.plan(Behaviour.TIMEOUT_ACCEPTED, then=Behaviour.PARTIAL)
    lease = store.acquire_lease("gw", T0, TTL)
    assert lease is not None
    store.accept_intent(intent("a"), unit_price=D("65000"), budget=D("5000"))
    [(oid, claimed)] = store.claim_outbox(lease.token, T0)
    order = Order(claimed)
    gateway.submit(order, ex)
    store.record_order(order, lease.token, T0, "submit")
    assert order.state is OrderState.UNKNOWN

    # restart: a new writer reloads unresolved orders and reconciles; the outbox row is not handed out again
    later = T0 + timedelta(minutes=5)
    lease2 = store.acquire_lease("gw-restarted", later, TTL)
    assert lease2 is not None and lease2.token == lease.token + 1
    assert store.claim_outbox(lease2.token, later) == []
    [again] = store.unresolved_orders()
    assert again.state is OrderState.UNKNOWN and again.intent == claimed
    gateway.reconcile(again, ex)
    store.record_order(again, lease2.token, later, "reconcile")
    store.record_order(again, lease2.token, later, "reconcile")       # repeated write changes nothing
    assert ex.submits == 1
    loaded = store.load_order(again.client_order_id)
    assert loaded.state is OrderState.PARTIALLY_FILLED and loaded.filled_qty == D("0.01") and len(loaded.fills) == 1
    assert store.reserved() == D("0.02") * D("65000")

    ex.cancel("BTCUSDT", again.client_order_id)
    gateway.reconcile(again, ex)
    store.record_order(again, lease2.token, later, "canceled")
    store.finish_outbox(oid, lease2.token, later)
    assert store.unresolved_orders() == [] and store.reserved() == 0
    states = [s for s, _ in store.load_order(again.client_order_id).history]
    assert states == [OrderState.INTENT, OrderState.UNKNOWN, OrderState.PARTIALLY_FILLED, OrderState.CANCELED]


def test_fills_and_events_are_append_only(store, db):  # noqa: F811
    lease = store.acquire_lease("gw", T0, TTL)
    assert lease is not None
    store.accept_intent(intent("a"), unit_price=D("65000"), budget=D("5000"))
    cid = intent("a").client_order_id
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        db.execute("update trading.exec_order_events set reason = 'x' where client_order_id = %s", (cid,))
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        db.execute("delete from trading.exec_intents where client_order_id = %s", (cid,))
