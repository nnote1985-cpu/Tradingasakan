"""Testnet plan P4: the worker's executor on a throwaway local PostgreSQL and an in-memory Binance (§5.4, §8.2–§8.4; RT08, RT31, RT40)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tests.fake_binance import FakeBinance
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.contracts.enums import TradingMode
from trading.execution.binance_spot import BinanceSpotTestnet
from trading.execution.intent import OrderIntent
from trading.execution.orders import OrderState
from trading.execution.safety import AccountSafety, SafetyState
from trading.execution.store import ExecStore
from trading.execution.types import Side
from trading.ingest.account import Balance
from trading.ingest.quotes import Quote
from trading.runtime.alerts import Severity
from trading.runtime.conformance import BOOK, Conformance
from trading.runtime.executor import Executor
from trading.runtime.persist import PersistStore, Recorder

psycopg = pytest.importorskip("psycopg")


class Alerts:
    def __init__(self) -> None:
        self.items: list[tuple[Severity, str, str]] = []

    def push(self, severity: Severity, key: str, text: str) -> None:
        self.items.append((severity, key, text))

    def keys(self) -> list[str]:
        return [k for _, k, _ in self.items]


class Rig:
    """Executor + stores + fake exchange with a hand-driven clock. Each test starts on its own day, so client ids never collide."""

    _day = [0]

    def __init__(self, db, conformance: bool = True, verdict: str | None = "MATCH") -> None:  # noqa: F811
        Rig._day[0] += 1
        self.t = datetime(2027, 1, 1, 0, 0, 5, tzinfo=UTC) + timedelta(days=Rig._day[0])
        self.mono_s = 1000.0
        self.account = f"p4-{datetime.now(UTC).timestamp()}"
        self.store = ExecStore(db, self.account)
        self.persist = PersistStore(db, self.account)
        self.ex = FakeBinance()
        self.adapter = BinanceSpotTestnet(self.ex)
        self.alerts = Alerts()
        self.verdict = verdict
        self.token = self.store.acquire_lease("test", self.t, timedelta(days=30)).token
        self.executor = self.make(conformance)

    def make(self, conformance: bool = True) -> Executor:
        return Executor(self.store, self.adapter, self.alerts, persist=self.persist, conformance=Conformance() if conformance else None,  # type: ignore[arg-type]
                        quote=self.quote, verdict=lambda: self.verdict, now=lambda: self.t, mono=lambda: self.mono_s, log=lambda _m: None)

    def quote(self) -> Quote:
        return Quote("BTCUSDT", self.ex.bid, D("1"), self.ex.ask, D("1"), 1, int(self.t.timestamp() * 1000))

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
        self.mono_s += seconds

    def tick(self, n: int = 1, every: float = 2.0) -> None:
        for _ in range(n):
            self.executor.tick(self.token, ready=True)
            self.advance(every)

    def states(self) -> dict[str, list[str]]:
        rows = self.store.conn.execute(
            "select i.leg, e.state from trading.exec_order_events e join trading.exec_intents i using (client_order_id) "
            "where i.account = %s order by i.created_at, e.seq", (self.account,)).fetchall()
        out: dict[str, list[str]] = {}
        for leg, state in rows:
            out.setdefault(leg, []).append(state)
        return out

    def posts(self) -> int:
        return sum(1 for m, p, _ in self.ex.calls if m == "POST" and p == "/api/v3/order")


@pytest.fixture
def rig(db):  # noqa: F811
    r = Rig(db)
    r.executor.start(r.token)
    return r


def test_recovering_becomes_running_once_orders_and_exchange_agree(rig):
    assert rig.executor.safety.state is SafetyState.RECOVERING
    rig.tick()
    assert rig.executor.safety.state is SafetyState.RUNNING
    assert rig.store.load_safety().epoch == 1 and "entries" in rig.alerts.keys()
    row = rig.store.conn.execute("select state from trading.exec_safety where account = %s", (rig.account,)).fetchone()
    assert row == ("RUNNING",)


def test_a_manual_order_or_a_failed_account_check_keeps_entries_blocked(db):  # noqa: F811
    r = Rig(db, verdict="DISCREPANCY")
    r.executor.start(r.token)
    r.tick()
    assert r.executor.safety.state is SafetyState.RECOVERING and r.posts() == 0
    r.verdict = "MATCH"
    r.ex.manual_order()
    r.advance(10)
    r.tick()
    assert r.executor.safety.state is SafetyState.RECOVERING and "foreign-orders" in r.alerts.keys()


def test_conformance_cycle_round_trip(rig):
    rig.tick(12)
    st = rig.states()
    assert st["mkt-buy"] == ["INTENT", "SUBMITTING", "FILLED"]
    assert st["mkt-sell"] == ["INTENT", "SUBMITTING", "FILLED"]
    assert st["limit-far"] == ["INTENT", "SUBMITTING", "ACKNOWLEDGED", "CANCELED"]
    assert st["limit-near"] == ["INTENT", "SUBMITTING", "ACKNOWLEDGED"]
    near = next(o for o in rig.executor.orders.values() if o.intent.leg == "limit-near")
    rig.ex.fill(near.client_order_id, D("0.00005"))                     # a partial fill while it waits
    rig.tick(3, every=5)
    assert rig.states()["limit-near"][-1] == "PARTIALLY_FILLED"
    rig.advance(600)
    rig.tick(4)
    st = rig.states()
    assert st["limit-near"][-1] == "CANCELED" and "near-sell" not in st     # 0.00005 BTC is below min notional
    body = rig.executor.body()
    assert body["conformance"]["cycles_done"] == 1 and body["open_orders"] == 0 and body["unresolved"] == []
    assert body["counts"]["filled"] == 2 and body["counts"]["canceled"] == 2
    fills = rig.store.conn.execute("select count(*) from trading.exec_fills f join trading.exec_intents i using (client_order_id) "
                                   "where i.account = %s", (rig.account,)).fetchone()[0]
    assert fills == 3 and rig.store.reserved() == 0
    rig.tick(5)
    assert rig.posts() == 4                                              # nothing more until the next 4-hour slot


def test_submit_timeout_after_accept_is_reconciled_not_resent(rig):
    rig.ex.plan("timeout_accepted")
    rig.tick()                                                           # RUNNING, then mkt-buy accepted but the answer is lost
    assert rig.states()["mkt-buy"] == ["INTENT", "SUBMITTING", "UNKNOWN"]
    rig.tick()
    assert rig.states()["mkt-buy"] == ["INTENT", "SUBMITTING", "UNKNOWN", "FILLED"]
    buy = next(o for o in rig.ex.orders.values() if o.side == "BUY")
    assert sum(1 for m, _, p in rig.ex.calls if m == "POST" and p["newClientOrderId"] == buy.client_id) == 1


def test_an_order_never_found_pauses_entries_and_alerts_but_is_not_given_up(rig):
    rig.ex.plan("timeout_lost")
    rig.tick()
    rig.tick(16)                                                         # 32 s of "not found"
    assert rig.executor.safety.state is SafetyState.SOFT_PAUSED
    assert any(s is Severity.CRITICAL and k.startswith("unknown-") for s, k, _ in rig.alerts.items)
    [u] = rig.executor.body()["unresolved"]
    assert u["state"] == "RECONCILING" and u["for_s"] >= 30
    assert rig.posts() == 1 and rig.store.reserved() > 0                 # reservation kept, nothing resent


def test_restart_after_submitting_was_written_reconciles_instead_of_resending(db):  # noqa: F811
    r = Rig(db, conformance=False)
    s = AccountSafety()
    s.recovered(r.t, reconciled=True)
    r.store.save_safety(s)
    rules = r.adapter.get_trading_rules("BTCUSDT")
    it = OrderIntent.build(rules, side=Side.BUY, qty=D("0.0001"), mode=TradingMode.TESTNET, book=f"crash/{r.account}", decision_id="d",
                           leg="a", created_at=r.t, safety_epoch=1)
    r.store.accept_intent(it, unit_price=D("65000"), budget=D("1000"))
    [(oid, claimed)] = r.store.claim_outbox(r.token, r.t)
    from trading.execution.orders import Order

    o = Order(claimed)
    o.transition(OrderState.SUBMITTING, "submit")
    r.store.record_order(o, r.token, r.t, "submit")
    r.adapter.submit(claimed)                                           # reached the exchange, then the worker died
    token2 = r.store.acquire_lease("test2", r.t + timedelta(days=31), timedelta(days=60)).token
    r.t += timedelta(days=31)
    r.token = token2
    r.executor = r.make(conformance=False)
    r.executor.start(token2)
    r.tick()
    assert r.states()["a"] == ["INTENT", "SUBMITTING", "UNKNOWN", "FILLED"]
    assert r.posts() == 1 and r.executor.safety.state is SafetyState.RUNNING


def test_an_intent_claimed_by_a_stopped_writer_but_never_sent_expires(db):  # noqa: F811
    r = Rig(db, conformance=False)
    s = AccountSafety()
    s.recovered(r.t, reconciled=True)
    r.store.save_safety(s)
    rules = r.adapter.get_trading_rules("BTCUSDT")
    it = OrderIntent.build(rules, side=Side.BUY, qty=D("0.0001"), mode=TradingMode.TESTNET, book=f"stale/{r.account}", decision_id="d",
                           leg="a", created_at=r.t, safety_epoch=1)
    r.store.accept_intent(it, unit_price=D("65000"), budget=D("1000"))
    r.store.claim_outbox(r.token, r.t)
    r.executor.start(r.token)
    assert r.states()["a"] == ["INTENT", "EXPIRED"] and r.store.reserved() == 0 and r.posts() == 0


def test_intent_past_its_ttl_is_not_sent(db):  # noqa: F811
    r = Rig(db, conformance=False)
    s = AccountSafety()
    s.recovered(r.t, reconciled=True)
    r.store.save_safety(s)
    rules = r.adapter.get_trading_rules("BTCUSDT")
    it = OrderIntent.build(rules, side=Side.SELL, qty=D("0.0001"), mode=TradingMode.TESTNET, book=f"ttl/{r.account}", decision_id="d",
                           leg="a", created_at=r.t - timedelta(minutes=5), safety_epoch=1)
    r.store.accept_intent(it, unit_price=D("65000"), budget=D("1000"))
    r.executor.start(r.token)
    r.tick()
    assert r.states()["a"] == ["INTENT", "EXPIRED"] and r.posts() == 0


def test_trades_made_while_the_worker_was_down_are_backfilled_once(db):  # noqa: F811
    r = Rig(db, conformance=False)
    r.persist.start_epoch({"BTC": Balance(D("1"), D("0")), "USDT": Balance(D("10000"), D("0"))}, r.t - timedelta(hours=1))
    r.ex.now_ms = int(r.t.timestamp() * 1000)
    rules = r.adapter.get_trading_rules("BTCUSDT")
    for leg in ("a", "b"):
        r.adapter.submit(OrderIntent.build(rules, side=Side.BUY, qty=D("0.0001"), mode=TradingMode.TESTNET, book=f"bf/{r.account}",
                                           decision_id="d", leg=leg, created_at=r.t, safety_epoch=1))
    rec = Recorder(r.persist)
    assert r.executor.backfill_trades() == 2
    rec.flush(r.executor.drain_records())
    assert rec.counts["trades"] == 2
    r.adapter.submit(OrderIntent.build(rules, side=Side.SELL, qty=D("0.0001"), mode=TradingMode.TESTNET, book=f"bf/{r.account}",
                                       decision_id="d", leg="c", created_at=r.t, safety_epoch=1))
    assert r.executor.backfill_trades() == 1                              # only the trade after the last stored id
    rec.flush(r.executor.drain_records())
    n = r.store.conn.execute("select count(*) from trading.exec_trades where account = %s", (r.account,)).fetchone()[0]
    assert n == 3
    assert BOOK == "tn-conformance-v1"


def test_web_activity_lists_orders_with_their_last_state_and_trades(db):  # noqa: F811
    from api.workers import ORDERS_SQL, TRADES_SQL, activity_items

    r = Rig(db)
    r.persist.start_epoch({"BTC": Balance(D("1"), D("0")), "USDT": Balance(D("10000"), D("0"))}, r.t - timedelta(hours=1))
    r.ex.now_ms = int(r.t.timestamp() * 1000)
    r.executor.start(r.token)
    r.tick(12)
    rec = Recorder(r.persist)
    r.executor.backfill_trades()
    rec.flush(r.executor.drain_records())
    orders = r.store.conn.execute(ORDERS_SQL, (r.account, 50)).fetchall()
    trades = r.store.conn.execute(TRADES_SQL, (r.account, 50)).fetchall()
    out = activity_items(orders, trades)
    by_leg = {o["leg"]: o for o in out["orders"]}
    assert by_leg["mkt-buy"]["state"] == "FILLED" and by_leg["mkt-buy"]["avg_price"] == "65000.01"
    assert by_leg["mkt-buy"]["qty"] == "0.0001" and by_leg["limit-far"]["state"] == "CANCELED"
    assert by_leg["limit-far"]["avg_price"] is None and by_leg["limit-near"]["limit_price"] == "65000"
    assert [t["side"] for t in out["trades"]] and {t["leg"] for t in out["trades"]} == {"mkt-buy", "mkt-sell"}
    assert out["trades"][0]["quote_qty"] in ("6.5", "6.500001") and out["trades"][0]["book"] == BOOK
    from api.workers import BALANCES_SQL, PNL_ASSETS, PNL_SQL, pnl_items

    bal = r.store.conn.execute(BALANCES_SQL, (list(PNL_ASSETS), r.account)).fetchall()
    books = r.store.conn.execute(PNL_SQL, (r.account,)).fetchall()
    p = pnl_items(bal, books, "65000")
    [b] = p["books"]
    assert b["book"] == BOOK and (b["buys"], b["sells"]) == (1, 1) and p["epoch"] == 1
    # bought 0.0001 at 65000.01, sold 0.0001 at 65000, fees in BNB: -0.000001 USDT
    assert b["btc_net"] == "0" and b["usdt_net"] == "-0.000001" and b["pnl_usdt"] == "0" and b["bnb_fees"] == "0.00002"
    assert [x["asset"] for x in p["balances"]] == ["BTC", "USDT"] and p["balances"][1]["opening"] == "10000"
