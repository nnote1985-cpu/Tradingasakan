"""Testnet plan P5: Guardian on a throwaway local PostgreSQL and an in-memory Binance (§8.5; the HALT drill)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

import pytest

from tests.integration.test_executor_p4 import Rig
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent
from trading.execution.safety import SafetyState
from trading.execution.store import IntentRefused
from trading.execution.types import OrderType, Side
from trading.runtime.alerts import Severity
from trading.runtime.executor import Executor
from trading.runtime.guardian import GuardedBook, Guardian

pytest.importorskip("psycopg")

ALLOCATION = D("250")


class GuardRig(Rig):
    def make(self, conformance: bool = False) -> Executor:
        self.book_name = f"tn-guard-{self.account}"                       # client ids are global: one book per test
        ex = super().make(conformance=False)
        ex.guardian = Guardian(self.store, [GuardedBook(self.book_name, ALLOCATION)])
        return ex

    def price(self, bid: str) -> None:
        self.ex.bid, self.ex.ask = D(bid), D(bid) + D("0.01")

    def order(self, leg: str, side: Side, qty: str, limit: str | None = None) -> str:
        intent = OrderIntent(mode=TradingMode.TESTNET, book=self.book_name, decision_id="d1", leg=leg, symbol="BTCUSDT", side=side,
                             order_type=OrderType.LIMIT if limit else OrderType.MARKET, qty=D(qty),
                             limit_price=D(limit) if limit else None, created_at=self.t, safety_epoch=self.executor.safety.epoch)
        assert self.store.accept_intent(intent, unit_price=self.ex.ask, budget=ALLOCATION)
        return intent.client_order_id

    def events(self) -> list[str]:
        rows = self.store.conn.execute("select kind from trading.exec_risk_events where account = %s order by id", (self.account,))
        return [r[0] for r in rows]

    def requests(self) -> list[tuple[str, str | None]]:
        rows = self.store.conn.execute("select kind, outcome from trading.exec_operator_requests where account = %s order by id",
                                       (self.account,))
        return [(k, o) for k, o in rows]

    def book(self) -> dict:
        return self.executor.body()["guardian"]["books"][0]


@pytest.fixture
def g(db):  # noqa: F811
    r = GuardRig(db)
    r.executor.start(r.token)
    r.tick()                                                             # RECOVERING -> RUNNING; first mark is the high-water mark
    assert r.executor.safety.state is SafetyState.RUNNING
    r.order("buy", Side.BUY, "0.003")                                    # about 195 USDT of BTC
    r.tick()
    return r


def test_alerts_once_then_halt_cancels_buys_sells_and_confirms_flat(g):
    resting = g.order("rest", Side.BUY, "0.0001", limit="64500")
    g.tick(3)
    assert g.executor.orders[resting].is_open and g.book()["equity"] is not None
    g.price("52000")                                                     # about 15.6% below the high, 15.6% down on the day
    g.tick(3)
    assert g.events() == ["DAY_LOSS_ALERT", "DRAWDOWN_ALERT"]            # once each, not every tick
    assert g.executor.safety.state is SafetyState.RUNNING
    g.price("40000")                                                     # 30% drawdown: past the 25% spot.tn.v1 HALT
    g.tick()
    assert g.store.load_safety().epoch == 2 and "HALT" in g.events()
    assert any(s is Severity.CRITICAL and k == "guardian-halt" for s, k, _ in g.alerts.items)
    assert g.ex.orders[resting].status == "CANCELED"
    g.tick(3)
    assert g.executor.safety.state is SafetyState.HALTED
    assert g.events()[-2:] == ["HALT", "FLAT_CONFIRMED"]
    sells = [o for o in g.ex.orders.values() if o.side == "SELL"]
    assert [(o.type, o.qty, o.status) for o in sells] == [("MARKET", D("0.003"), "FILLED")]
    assert g.store.position(g.book_name, "BTCUSDT", "BTC", "USDT")[0] == 0
    with pytest.raises(IntentRefused, match="ENTRIES_BLOCKED_HALTED"):
        g.order("again", Side.BUY, "0.0001")


def test_resume_needs_halted_and_a_named_operator_then_reconciles_and_restarts_the_high(g):
    g.price("40000")
    g.tick()
    assert g.executor.safety.state is SafetyState.HALTING
    g.store.request("RESUME", "note", "too early")
    g.tick(3)
    assert g.requests()[0][1].startswith("refused: account is HALTING")
    assert g.executor.safety.state is SafetyState.HALTED                # the refused resume changed nothing
    g.store.request("RESUME", "note", "drill done")
    g.verdict = "DISCREPANCY"                                            # RUNNING needs the account check, not only the request
    g.tick()
    assert g.executor.safety.state is SafetyState.RECOVERING and g.events()[-1] == "RESUME"
    g.verdict = "MATCH"
    g.advance(10)
    g.tick()
    assert g.executor.safety.state is SafetyState.RUNNING
    b = g.book()
    assert b["hwm"] == b["equity"] and D(b["drawdown"]) == 0             # the high restarts from equity at resume
    assert g.events() == ["DAY_LOSS_ALERT", "DRAWDOWN_ALERT", "HALT", "FLAT_CONFIRMED", "RESUME"]   # resume never clears history


def test_operator_halt_drill_and_halt_survives_a_restart(g):
    g.store.request("HALT", "note", "P5 HALT drill")
    g.tick(3)
    assert g.executor.safety.state is SafetyState.HALTED
    assert g.events() == ["HALT", "FLAT_CONFIRMED"] and g.requests()[0][1].startswith("halted at epoch 2")
    assert [o.side for o in g.ex.orders.values()] == ["BUY", "SELL"]
    g.store.request("HALT", "note", "twice")
    g.tick()
    assert g.requests()[1][1] == "already HALTED" and g.store.load_safety().epoch == 2
    hwm = g.book()["hwm"]
    g.executor = g.make()                                                # a restart: same store, new process state
    g.executor.start(g.token)
    g.advance(10)
    g.tick(3)
    assert g.executor.safety.state is SafetyState.HALTED and g.book()["hwm"] == hwm
    row = g.store.conn.execute("select profile, allocation from trading.exec_book_risk where account = %s", (g.account,)).fetchone()
    assert row == ("spot.tn.v1", ALLOCATION)


def test_risk_events_are_append_only(g):
    g.store.request("HALT", "note", "drill")
    g.tick()
    with pytest.raises(Exception, match="append-only|not allowed|forbid"):
        g.store.conn.execute("delete from trading.exec_risk_events where account = %s", (g.account,))
    with pytest.raises(Exception, match="check"):
        g.store.request("HALT", "  ", "no actor")


def test_a_testnet_bid_far_from_mainnet_marks_nothing_and_alerts(g):
    ref = {"price": D("65000"), "age": 30}
    g.executor.reference = lambda: (ref["price"], g.t - timedelta(seconds=ref["age"]))
    hwm = g.book()["hwm"]
    g.price("40000")                                                     # 38% below Mainnet: a thin-book glitch, not the market
    g.tick(3)
    assert g.executor.safety.state is SafetyState.RUNNING and g.events() == []
    assert g.book()["hwm"] == hwm and "from Mainnet" in g.executor.body()["guardian"]["price_off"]
    assert any(k == "guardian-price" for _, k, _ in g.alerts.items)
    g.price("90000")                                                     # an upward glitch must not lift the high either
    g.tick(3)
    assert g.book()["hwm"] == hwm
    ref["age"] = 600                                                     # no recent Mainnet close: the bid is all there is
    g.price("40000")
    g.tick()
    assert "HALT" in g.events() and g.executor.body()["guardian"]["price_off"] is None
