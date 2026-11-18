"""Futures desk stage 2 (docs/futures-desk-plan.md §7): ticket checks, sizing, the executor and the API."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.main import create_app
from trading.config import Settings
from trading.contracts.enums import OperatorRole
from trading.futures.executor import Executor
from trading.futures.orders import Ticket, check_move_stop, liq_estimate, plan_open
from trading.futures.rest import FuturesDemoRest, FuturesError, OrdersDisabled
from trading.futures.risk import caps
from trading.futures.sync import RiskState, Snapshot
from trading.runtime.credentials import TestnetCredentials

T0 = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
MKT = {"BTCUSDT": {"mark": "80000", "tick_size": "0.1", "step_size": "0.001", "min_qty": "0.001", "min_notional": "100"},
       "ETHUSDT": {"mark": "3000", "tick_size": "0.01", "step_size": "0.001", "min_qty": "0.001", "min_notional": "20"}}
RISK = RiskState(date(2026, 11, 18), Decimal("10000"), Decimal("10000"), T0, Decimal("10000"), T0)
SL_ALGO = {"source": "algo", "id": "7", "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "kind": "SL", "price": "0",
           "trigger_price": "78000", "qty": "0", "close_position": True, "reduce_only": False, "created_at": None}
POS = {"symbol": "BTCUSDT", "side": "LONG", "qty": "0.05", "entry_price": "79000", "mark_price": "80000",
       "liquidation_price": "60000", "unrealized_pnl": "50"}


def plan(ticket, equity="10000", available="9000", positions=(), orders=(), risk=RISK):
    return plan_open(ticket, equity=Decimal(equity), available=Decimal(available), positions=list(positions), orders=list(orders),
                     markets=MKT, caps=caps(Decimal(equity), risk) if risk else None)


# --- sizing and checks ------------------------------------------------------

def test_size_is_one_percent_to_the_stop_floored_to_step():
    p = plan(Ticket("BTCUSDT", "LONG", Decimal("79200"), 5))
    assert p.ok, p.refusals
    # 100 USDT / 800 per BTC = 0.125 BTC
    assert p.qty == Decimal("0.125") and p.risk_usdt == Decimal("100") and p.risk_pct == Decimal("0.01")
    assert p.notional == Decimal("10000") and p.margin == Decimal("2000")
    short = plan(Ticket("ETHUSDT", "SHORT", Decimal("3033.333"), 3, take_profit=Decimal("2900.005")))
    assert short.ok and short.stop_price == Decimal("3033.34") and short.take_profit == Decimal("2900.00")
    assert short.qty == Decimal("2.999") and short.risk_usdt <= 100


@pytest.mark.parametrize(("ticket", "words"), [
    (Ticket("BTCUSDT", "LONG", Decimal("80500"), 5), "ต่ำกว่าราคาตอนนี้"),
    (Ticket("BTCUSDT", "SHORT", Decimal("79000"), 5), "สูงกว่าสำหรับ Short"),
    (Ticket("BTCUSDT", "LONG", Decimal("79990"), 5), "ใกล้ราคาเกินไป"),
    (Ticket("BTCUSDT", "LONG", Decimal("60000"), 2), "ห่างเกิน 15%"),
    (Ticket("BTCUSDT", "LONG", Decimal("79000"), 5, take_profit=Decimal("79500")), "Take-profit"),
    (Ticket("DOGEUSDT", "LONG", Decimal("1"), 5), "เฉพาะ"),
    (Ticket("BTCUSDT", "LONG", Decimal("79000"), 11), "Leverage"),
    (Ticket("BTCUSDT", "LONG", Decimal("76000"), 10), "Liquidation"),   # 10x liq ~ 72,400... stop 76,000 is fine? see below
])
def test_refusals(ticket, words):
    p = plan(ticket)
    if words == "Liquidation":
        # 10x: liquidation estimate 80000 * (1 - 0.1 + 0.005) = 72,400, stop 76,000 is inside it, so this one passes;
        # 20x is beyond MAX_LEVERAGE, so check the estimate directly against a stop past it.
        assert p.ok
        assert liq_estimate("LONG", Decimal("80000"), 10) == Decimal("72400.000")
        p = plan(Ticket("BTCUSDT", "LONG", Decimal("72000"), 10))
    assert not p.ok and any(words in r for r in p.refusals), p.refusals


def test_margin_and_minimums():
    p = plan(Ticket("BTCUSDT", "LONG", Decimal("79900"), 1), available="5000")    # 1 BTC: 80,000 notional at 1x
    assert not p.ok and any("Margin" in r for r in p.refusals)
    small = RiskState(date(2026, 11, 18), Decimal("50"), Decimal("50"), T0, Decimal("50"), T0)
    p = plan(Ticket("BTCUSDT", "LONG", Decimal("68000"), 2), equity="50", available="50", risk=small)  # 0.5 USDT risk: qty rounds to 0
    assert not p.ok and any("ขั้นต่ำ" in r for r in p.refusals)


def test_refused_when_cap_reached_position_open_unprotected_or_leftover_orders():
    blocked = plan(Ticket("BTCUSDT", "LONG", Decimal("79000"), 5), equity="9690")
    assert any("เพดาน" in r for r in blocked.refusals)
    open_already = plan(Ticket("BTCUSDT", "SHORT", Decimal("81000"), 5), positions=[POS], orders=[SL_ALGO])
    assert any("เปิดอยู่แล้ว" in r for r in open_already.refusals)
    bare = plan(Ticket("ETHUSDT", "LONG", Decimal("2950"), 5), positions=[POS])
    assert any("ไม่มี Stop-loss" in r for r in bare.refusals)
    leftover = plan(Ticket("BTCUSDT", "LONG", Decimal("79000"), 5), orders=[SL_ALGO])
    assert any("คำสั่งค้าง" in r for r in leftover.refusals)
    no_caps = plan(Ticket("BTCUSDT", "LONG", Decimal("79000"), 5), risk=None)
    assert any("เพดาน" in r for r in no_caps.refusals)


def test_size_shrinks_to_the_room_left_under_a_cap():
    near = RiskState(date(2026, 11, 18), Decimal("10000"), Decimal("10000"), T0, Decimal("10000"), T0)
    p = plan(Ticket("BTCUSDT", "LONG", Decimal("79200"), 5), equity="9750", risk=near)   # day room = 9750 - 9700 = 50
    assert p.ok and p.risk_usdt <= Decimal("50") and p.qty == Decimal("0.062") and p.notes


def test_move_stop_only_toward_less_risk():
    stop, r = check_move_stop(POS, Decimal("79500"), Decimal("10000"), Decimal("80000"), Decimal("0.1"))
    assert stop == Decimal("79500") and r == []                   # above entry: locks in a gain
    _, r = check_move_stop(POS, Decimal("76000"), Decimal("10000"), Decimal("80000"), Decimal("0.1"))
    assert any("เกิน 1%" in x for x in r)                          # 3,000 x 0.05 = 150 = 1.5%
    _, r = check_move_stop(POS, Decimal("80100"), Decimal("10000"), Decimal("80000"), Decimal("0.1"))
    assert any("ฝั่งขาดทุน" in x for x in r)


# --- REST gate --------------------------------------------------------------

def test_rest_refuses_order_calls_unless_enabled_and_ignores_margin_type_noop():
    sent = []
    rest = FuturesDemoRest(TestnetCredentials(api_key="k", secret="s"), send=lambda m, u, h: sent.append((m, u)) or (200, {}, {}))
    with pytest.raises(OrdersDisabled):
        rest.market_order("BTCUSDT", "BUY", "0.1", "x")
    assert sent == []
    rest = FuturesDemoRest(TestnetCredentials(api_key="k", secret="s"), orders_enabled=True,
                           send=lambda m, u, h: sent.append((m, u)) or (400, {"code": -4046, "msg": "No need to change margin type."}, {}))
    rest.set_margin_isolated("BTCUSDT")
    assert sent[0][0] == "POST" and sent[0][1].startswith("https://demo-fapi.binance.com/fapi/v1/marginType?")


def test_stop_order_goes_to_algo_endpoint_with_close_position_on_mark_price():
    sent = []
    rest = FuturesDemoRest(TestnetCredentials(api_key="k", secret="s"), orders_enabled=True,
                           send=lambda m, u, h: sent.append((m, u)) or (200, {"algoId": 1}, {}))
    rest.conditional_close("BTCUSDT", "SELL", "STOP_MARKET", "79200", "fd1s")
    m, url = sent[0]
    assert m == "POST" and "/fapi/v1/algoOrder?" in url
    for part in ("algoType=CONDITIONAL", "type=STOP_MARKET", "triggerPrice=79200", "closePosition=true", "workingType=MARK_PRICE",
                 "clientAlgoId=fd1s"):
        assert part in url
    with pytest.raises(ValueError):
        rest.conditional_close("BTCUSDT", "SELL", "LIMIT", "1", "x")


# --- executor ---------------------------------------------------------------

class Rest:
    def __init__(self, fail_stop=False, fill="0.125"):
        self.calls, self.fail_stop, self.fill = [], fail_stop, fill

    def set_margin_isolated(self, symbol):
        self.calls.append(("margin", symbol))

    def set_leverage(self, symbol, lev):
        self.calls.append(("lev", symbol, lev))

    def market_order(self, symbol, side, qty, cid, reduce_only=False):
        self.calls.append(("market", symbol, side, qty, cid, reduce_only))
        return {"orderId": 1, "status": "FILLED", "executedQty": self.fill if not reduce_only else qty, "avgPrice": "80010"}

    def conditional_close(self, symbol, side, kind, trigger, cid):
        self.calls.append(("cond", symbol, side, kind, trigger, cid))
        if self.fail_stop and kind == "STOP_MARKET":
            raise FuturesError("/fapi/v1/algoOrder", 400, {"code": -2021, "msg": "Order would immediately trigger."})
        return {"algoId": 9}

    def cancel_algo(self, algo_id):
        self.calls.append(("cancel_algo", algo_id))

    def cancel_all(self, symbol):
        self.calls.append(("cancel_all", symbol))


class Store:
    def __init__(self, reqs):
        self.reqs = reqs
        self.marks = []

    def open_requests(self):
        return [r for r in self.reqs if r["status"] in ("PENDING", "SENT")]

    def mark_request(self, rid, status, outcome=None, detail=None):
        r = next(x for x in self.reqs if x["id"] == rid)
        if r["status"] not in ("PENDING", "SENT") or (status == "SENT" and r["status"] != "PENDING"):
            return False
        r["status"] = status
        self.marks.append((rid, status, outcome))
        return True


class Alerts:
    def __init__(self):
        self.items = []

    def push(self, severity, key, text):
        self.items.append((severity.value, key, text))


def snap(positions=(), orders=(), equity="10000"):
    return Snapshot(T0, Decimal(equity), Decimal(equity), Decimal("9000"), Decimal(0), list(positions), list(orders), MKT)


def req(i=1, kind="OPEN", **kw):
    base = {"id": i, "kind": kind, "symbol": "BTCUSDT", "side": "LONG", "stop_price": Decimal("79200"), "take_profit": Decimal("82000"),
            "leverage": 5, "qty": Decimal("0.125"), "ref_price": Decimal("80000"), "actor": "web:x", "requested_at": T0, "status": "PENDING"}
    return base | kw


def test_open_sends_entry_then_stop_then_tp_and_records_done():
    rest, store, alerts = Rest(), Store([req()]), Alerts()
    assert Executor(rest, store, alerts, log=lambda m: None).run(snap(), RISK, T0 + timedelta(seconds=3)) == 1
    assert [c[0] for c in rest.calls] == ["margin", "lev", "market", "cond", "cond"]
    assert rest.calls[2] == ("market", "BTCUSDT", "BUY", "0.125", "fd1e", False)
    assert rest.calls[3] == ("cond", "BTCUSDT", "SELL", "STOP_MARKET", "79200", "fd1s")
    assert rest.calls[4][3] == "TAKE_PROFIT_MARKET"
    assert [m[1] for m in store.marks] == ["SENT", "DONE"]


def test_open_never_sends_more_than_previewed():
    rest, store = Rest(), Store([req(qty=Decimal("0.1"))])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap(), RISK, T0)
    assert rest.calls[2][3] == "0.1"


def test_failed_stop_closes_the_position_at_once():
    rest, store, alerts = Rest(fail_stop=True), Store([req()]), Alerts()
    Executor(rest, store, alerts, log=lambda m: None).run(snap(), RISK, T0)
    assert rest.calls[-1] == ("market", "BTCUSDT", "SELL", "0.125", "fd1x", True)
    assert store.marks[-1][1] == "FAILED" and "ปิด position ทันที" in store.marks[-1][2]
    assert alerts.items[-1][0] == "CRITICAL"


def test_open_is_rechecked_on_fresh_data_and_old_tickets_expire():
    rest, store = Rest(), Store([req(1), req(2, requested_at=T0 - timedelta(minutes=2))])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap(equity="9690"), RISK, T0)
    assert rest.calls == []
    assert store.marks == [(1, "REFUSED", store.marks[0][2]), (2, "EXPIRED", store.marks[1][2])] and "เพดาน" in store.marks[0][2]


def test_sent_rows_from_an_earlier_run_are_failed_not_resent():
    rest, store, alerts = Rest(), Store([req(status="SENT")]), Alerts()
    ex = Executor(rest, store, alerts, log=lambda m: None)
    ex.run(snap(), RISK, T0)
    assert rest.calls == [] and store.marks == [(1, "FAILED", store.marks[0][2])] and alerts.items


def test_close_market_reduce_only_then_cancels_orders():
    rest, store = Rest(), Store([req(kind="CLOSE", side=None)])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap([POS], [SL_ALGO]), RISK, T0)
    assert rest.calls == [("market", "BTCUSDT", "SELL", "0.05", "fd1c", True), ("cancel_all", "BTCUSDT")]
    assert store.marks[-1][1] == "DONE"


def test_close_with_nothing_is_refused_and_close_works_even_when_blocked():
    rest, store = Rest(), Store([req(kind="CLOSE", symbol="ETHUSDT")])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap([POS], [SL_ALGO]), RISK, T0)
    assert store.marks[-1][1] == "REFUSED" and rest.calls == []
    rest, store = Rest(), Store([req(kind="CLOSE")])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap([POS], [SL_ALGO], equity="9000"), RISK, T0)
    assert store.marks[-1][1] == "DONE"


def test_move_stop_places_new_before_cancelling_old():
    rest, store = Rest(), Store([req(kind="MOVE_STOP", stop_price=Decimal("79500"))])
    Executor(rest, store, Alerts(), log=lambda m: None).run(snap([POS], [SL_ALGO]), RISK, T0)
    assert rest.calls == [("cond", "BTCUSDT", "SELL", "STOP_MARKET", "79500", "fd1s"), ("cancel_algo", "7")]


# --- API --------------------------------------------------------------------

VIEWER, OPERATOR = UUID(int=301), UUID(int=302)


class Verifier:
    def verify(self, token):
        try:
            return UUID(token)
        except ValueError as e:
            raise AuthError("invalid_token", "invalid") from e


class Roles:
    async def active_roles(self, user):
        return frozenset({OperatorRole.OPERATOR if user == OPERATOR else OperatorRole.VIEWER})


def row(orders_enabled=True, synced=None, positions=(), orders=()):
    return ("FUT1", synced or datetime.now(UTC), Decimal("10000"), Decimal("10000"), Decimal("9000"), Decimal("0"),
            list(positions), list(orders), MKT, {"orders_enabled": orders_enabled},
            RISK.day, RISK.day_open, RISK.hwm, RISK.hwm_at, RISK.start_equity, RISK.started_at)


class ApiStore:
    def __init__(self, r):
        self.r, self.added, self.seen = r, [], set()

    async def state_rows(self):
        return [self.r]

    async def history(self, account, since):
        return [], []

    async def requests(self, account):
        return []

    async def add_request(self, account, kind, symbol, actor, request_id, fields):
        if request_id in self.seen:
            return None
        self.seen.add(request_id)
        self.added.append((account, kind, symbol, actor, fields))
        return len(self.added)


def client(store):
    return TestClient(create_app(Settings(), Verifier(), Roles(), futures=store))


def h(user=OPERATOR):
    return {"Authorization": f"Bearer {user}"}


TICKET = {"symbol": "BTCUSDT", "side": "LONG", "stop_price": "79200", "leverage": 5}


def test_preview_for_viewers_submit_for_operators_only():
    store = ApiStore(row(orders_enabled=False))
    c = client(store)
    pv = c.post("/api/v1/futures/orders/preview", json=TICKET, headers=h(VIEWER)).json()
    assert pv["ok"] and pv["qty"] == "0.125" and pv["risk_usdt"] == "100"
    assert c.post("/api/v1/futures/orders", json=TICKET, headers=h(VIEWER)).status_code == 403
    r = c.post("/api/v1/futures/orders", json=TICKET, headers=h())
    assert r.status_code == 409 and r.json()["code"] == "orders_disabled" and store.added == []


def test_submit_queues_checked_ticket_once_per_request_id():
    store = ApiStore(row())
    c = client(store)
    rid = str(uuid4())
    r = c.post("/api/v1/futures/orders", json=TICKET | {"request_id": rid}, headers=h())
    assert r.status_code == 200 and r.json()["id"] == 1
    account, kind, symbol, actor, f = store.added[0]
    assert (account, kind, symbol, f["qty"], f["stop_price"]) == ("FUT1", "OPEN", "BTCUSDT", Decimal("0.125"), Decimal("79200"))
    assert actor == f"web:{OPERATOR}"
    again = c.post("/api/v1/futures/orders", json=TICKET | {"request_id": rid}, headers=h()).json()
    assert again["duplicate"] is True and len(store.added) == 1


def test_submit_refuses_bad_ticket_stale_data_and_schema():
    c = client(ApiStore(row()))
    bad = c.post("/api/v1/futures/orders", json=TICKET | {"stop_price": "81000"}, headers=h())
    assert bad.status_code == 422 and bad.json()["code"] == "ticket_refused" and bad.json()["reasons"]
    assert c.post("/api/v1/futures/orders", json=TICKET | {"leverage": 50}, headers=h()).status_code == 422
    assert c.post("/api/v1/futures/orders", json=TICKET | {"symbol": "DOGEUSDT"}, headers=h()).status_code == 422
    stale = client(ApiStore(row(synced=datetime.now(UTC) - timedelta(minutes=3))))
    assert stale.post("/api/v1/futures/orders", json=TICKET, headers=h()).json()["code"] == "stale"


def test_close_and_move_stop_endpoints():
    store = ApiStore(row(positions=[POS], orders=[SL_ALGO]))
    c = client(store)
    r = c.post("/api/v1/futures/positions/BTCUSDT/close", json={"request_id": str(uuid4())}, headers=h())
    assert r.status_code == 200 and store.added[-1][1] == "CLOSE"
    assert c.post("/api/v1/futures/positions/ETHUSDT/close", json={"request_id": str(uuid4())}, headers=h()).status_code == 422
    r = c.post("/api/v1/futures/positions/BTCUSDT/stop", json={"request_id": str(uuid4()), "stop_price": "79500"}, headers=h())
    assert r.status_code == 200 and store.added[-1][1] == "MOVE_STOP" and store.added[-1][4]["stop_price"] == Decimal("79500")
    wide = c.post("/api/v1/futures/positions/BTCUSDT/stop", json={"request_id": str(uuid4()), "stop_price": "70000"}, headers=h())
    assert wide.status_code == 422


def test_desk_says_who_can_trade():
    store = ApiStore(row())
    assert client(store).get("/api/v1/futures/desk", headers=h()).json()["can_trade"] is True
    assert client(store).get("/api/v1/futures/desk", headers=h(VIEWER)).json()["can_trade"] is False
