"""Futures desk stage 1 (docs/futures-desk-plan.md): guards, parsing, round trips, risk caps, worker, API view."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.futures import Unavailable, desk_view
from api.main import create_app
from trading.config import Settings
from trading.contracts.enums import OperatorRole
from trading.futures.rest import FuturesDemoRest, FuturesError, load_demo_credentials
from trading.futures.risk import caps, position_risk
from trading.futures.sync import RiskState, fill_row, mark_risk, markets, open_orders, positions, snapshot
from trading.futures.trades import round_trips
from trading.futures.worker import Config, FuturesWorker, LeaseLost
from trading.runtime import endpoints
from trading.runtime.credentials import CredentialError, TestnetCredentials

T0 = datetime(2026, 11, 18, 1, 0, tzinfo=UTC)


# --- endpoint guards --------------------------------------------------------

def test_futures_demo_is_signable_but_mainnet_futures_is_not():
    endpoints.check_signed(endpoints.rest_url(endpoints.Venue.FUTURES_DEMO, "/fapi/v3/account", "a=1"))
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.check_signed("https://fapi.binance.com/fapi/v3/account?a=1")
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.rest_url(endpoints.Venue.FUTURES_DEMO, "/api/v3/account")       # spot path on the futures host
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, "/fapi/v1/order")        # futures path on the spot host


def test_demo_credentials_refuse_mainnet_vars_and_need_one_secret():
    with pytest.raises(CredentialError, match="Mainnet"):
        load_demo_credentials({"FUTURES_DEMO_API_KEY": "k", "FUTURES_DEMO_API_SECRET": "s", "BINANCE_API_KEY": "x"})
    with pytest.raises(CredentialError, match="not set"):
        load_demo_credentials({})
    with pytest.raises(CredentialError, match="exactly one"):
        load_demo_credentials({"FUTURES_DEMO_API_KEY": "k"})
    c = load_demo_credentials({"FUTURES_DEMO_API_KEY": "abcdef", "FUTURES_DEMO_API_SECRET": "zzsecretzz"})
    assert "zzsecretzz" not in repr(c) and "abcdef" not in repr(c) and "abcd…" in repr(c)


def test_rest_signs_only_to_demo_host_and_stops_after_429():
    sent = []

    def send(method, url, headers):
        sent.append((method, url, headers))
        return (429, None, {"Retry-After": "30"}) if "userTrades" in url else (200, {"ok": 1}, {})

    rest = FuturesDemoRest(TestnetCredentials(api_key="k", secret="s"), send=send, wall_ms=lambda: 1_000_000)
    assert rest.account() == {"ok": 1}
    method, url, headers = sent[0]
    assert method == "GET" and url.startswith("https://demo-fapi.binance.com/fapi/v3/account?") and "signature=" in url
    assert headers == {"X-MBX-APIKEY": "k"}
    with pytest.raises(endpoints.RateLimited):
        rest.user_trades("BTCUSDT")
    with pytest.raises(endpoints.RateLimited):
        rest.account()          # still waiting: nothing sent
    assert len(sent) == 2


def test_rest_raises_binance_errors_with_code():
    rest = FuturesDemoRest(TestnetCredentials(api_key="k", secret="s"),
                           send=lambda m, u, h: (400, {"code": -2015, "msg": "Invalid API-key"}, {}))
    with pytest.raises(FuturesError) as e:
        rest.account()
    assert e.value.code == -2015 and "Invalid API-key" in str(e.value)


# --- parsing ----------------------------------------------------------------

INFO = {"symbols": [
    {"symbol": "BTCUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                                      {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                                      {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                                      {"filterType": "MIN_NOTIONAL", "notional": "100"}]},
    {"symbol": "DOGEUSDT", "filters": []},
]}
PREMIUM = [{"symbol": "BTCUSDT", "markPrice": "80000.00000000", "lastFundingRate": "0.00010000", "nextFundingTime": 1794988800000}]
POS = [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.050", "entryPrice": "79000.0", "breakEvenPrice": "79031.6",
        "markPrice": "80000.0", "unRealizedProfit": "50.0", "liquidationPrice": "63500.5", "isolatedMargin": "1350.0",
        "notional": "4000.0", "isolatedWallet": "1300.0", "initialMargin": "1333.3", "updateTime": 1794963600000},
       {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0", "markPrice": "3000", "liquidationPrice": "0"}]
ORDERS = [{"orderId": 11, "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "price": "85000", "origQty": "0.05",
           "reduceOnly": True, "closePosition": False, "stopPrice": "0", "time": 1794963600000}]
ALGOS = [{"algoId": 7, "symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET", "triggerPrice": "77000",
          "quantity": "0", "closePosition": True, "reduceOnly": False, "workingType": "MARK_PRICE", "createTime": 1794963600000}]


def test_markets_keep_only_desk_symbols_with_filters():
    m = markets(INFO, PREMIUM)
    assert list(m) == ["BTCUSDT"]
    assert m["BTCUSDT"] | {} == m["BTCUSDT"] and m["BTCUSDT"]["mark"] == "80000" and m["BTCUSDT"]["step_size"] == "0.001"
    assert m["BTCUSDT"]["tick_size"] == "0.1" and m["BTCUSDT"]["min_notional"] == "100"


def test_positions_skip_flat_and_read_side_margin_and_leverage():
    p = positions(POS, {"BTCUSDT": {"marginType": "ISOLATED", "leverage": 3}})
    assert len(p) == 1
    assert p[0] | {} == p[0]
    assert (p[0]["side"], p[0]["qty"], p[0]["liquidation_price"], p[0]["margin_type"], p[0]["leverage"], p[0]["margin"]) == \
        ("LONG", "0.05", "63500.5", "ISOLATED", 3, "1350")
    short = positions([POS[0] | {"positionAmt": "-0.010", "isolatedWallet": "0"}])
    assert short[0]["side"] == "SHORT" and short[0]["margin_type"] == "CROSS" and short[0]["leverage"] is None


def test_open_orders_tag_algo_stop_as_sl():
    o = open_orders(ORDERS, ALGOS)
    kinds = {(x["source"], x["kind"]) for x in o}
    assert kinds == {("order", "CLOSE"), ("algo", "SL")}
    sl = next(x for x in o if x["kind"] == "SL")
    assert sl["trigger_price"] == "77000" and sl["close_position"] is True
    assert open_orders([], {"orders": ALGOS})[0]["kind"] == "SL"   # tolerate a wrapped list


# --- round trips ------------------------------------------------------------

def F(i, side, qty, price, pnl="0", fee="0.5", at=0, order=None):
    return {"symbol": "BTCUSDT", "trade_id": i, "order_id": order or i, "side": side, "qty": Decimal(qty), "price": Decimal(price),
            "realized_pnl": Decimal(pnl), "commission": Decimal(fee), "commission_asset": "USDT",
            "traded_at": T0 + timedelta(minutes=at)}


def test_round_trip_long_with_partial_exits_and_funding():
    fills = [F(1, "BUY", "0.02", "80000", at=0), F(2, "BUY", "0.02", "81000", at=1),
             F(3, "SELL", "0.01", "82000", pnl="15", at=5), F(4, "SELL", "0.03", "83000", pnl="75", at=9)]
    funding = [{"kind": "FUNDING_FEE", "symbol": "BTCUSDT", "amount": Decimal("-1.2"), "asset": "USDT", "at": T0 + timedelta(minutes=8)},
               {"kind": "FUNDING_FEE", "symbol": "BTCUSDT", "amount": Decimal("-9"), "asset": "USDT", "at": T0 + timedelta(hours=9)}]
    [t] = round_trips(fills, funding)
    v = t.view()
    assert v["side"] == "LONG" and not v["open"] and v["qty"] == Decimal("0.04")
    assert v["entry_price"] == Decimal("80500") and v["exit_price"] == Decimal("82750")
    assert v["realized_pnl"] == 90 and v["fees"] == 2 and v["funding"] == Decimal("-1.2")
    assert v["net_pnl"] == Decimal("86.8") and v["fills"] == 4


def test_round_trip_flip_closes_and_opens_next_and_open_trade_stays_open():
    fills = [F(1, "BUY", "0.01", "80000", fee="0.4", at=0), F(2, "SELL", "0.03", "79000", pnl="-10", fee="1.2", at=3),
             F(3, "BUY", "0.01", "78000", pnl="20", at=6)]
    a, b = round_trips(fills, [])
    assert a.side == "LONG" and a.closed_at == T0 + timedelta(minutes=3)
    assert a.view()["net_pnl"] == Decimal("-10") - Decimal("0.4") - Decimal("0.4")   # its share of the flip fill's fee
    vb = b.view()
    assert vb["side"] == "SHORT" and vb["open"] and vb["entry_price"] == 79000 and vb["fees"] == Decimal("0.8") + Decimal("0.5")
    assert vb["realized_pnl"] == 20 and vb["exit_qty"] == Decimal("0.01")


# --- risk -------------------------------------------------------------------

def test_mark_risk_rolls_day_and_keeps_hwm():
    r = mark_risk(None, Decimal("10000"), T0)
    assert (r.day_open, r.hwm, r.start_equity) == (10000, 10000, 10000)
    r = mark_risk(r, Decimal("10500"), T0 + timedelta(hours=2))
    assert (r.day_open, r.hwm) == (10000, 10500)
    r = mark_risk(r, Decimal("9800"), T0 + timedelta(days=1))
    assert (r.day, r.day_open, r.hwm) == (date(2026, 11, 19), 9800, 10500)


def test_caps_levels_and_block():
    risk = RiskState(date(2026, 11, 18), Decimal("10000"), Decimal("10500"), T0, Decimal("10000"), T0)
    ok = caps(Decimal("9950"), risk)
    assert ok["day"]["level"] == "OK" and not ok["blocked"] and ok["day"]["room"] == Decimal("250")
    near = caps(Decimal("9780"), risk)              # 2.2% of the day = 73% of the 3% cap
    assert near["day"]["level"] == "NEAR" and not near["blocked"]
    day = caps(Decimal("9700"), risk)
    assert day["blocked"] and day["day"]["level"] == "BLOCKED" and "3.00%" in day["reasons"][0]
    total = caps(Decimal("9440"), RiskState(date(2026, 11, 18), Decimal("9440"), Decimal("10500"), T0, Decimal("10000"), T0))
    assert total["blocked"] and total["total"]["level"] == "BLOCKED" and total["day"]["level"] == "OK"


def test_position_risk_uses_tightest_stop_and_flags_rule():
    pos = positions(POS)[0]
    orders = open_orders([], ALGOS + [{**ALGOS[0], "algoId": 8, "triggerPrice": "78000"}])
    r = position_risk(pos, orders, Decimal("10000"))
    assert r["stop_price"] == Decimal("78000") and r["has_stop"]
    assert r["initial_risk"] == Decimal("50") and r["initial_risk_pct"] == Decimal("0.005") and not r["over_rule"]
    assert r["loss_at_stop"] == Decimal("100") and r["stop_before_liq"]
    bare = position_risk(pos, [], Decimal("10000"))
    assert bare["has_stop"] is False and "initial_risk" not in bare
    wide = position_risk(pos, open_orders([], [{**ALGOS[0], "triggerPrice": "75000"}]), Decimal("10000"))
    assert wide["initial_risk_pct"] == Decimal("0.02") and wide["over_rule"]


# --- worker -----------------------------------------------------------------

class FakeRest:
    def __init__(self):
        self.trades = {"BTCUSDT": [{"id": 1, "orderId": 5, "symbol": "BTCUSDT", "side": "BUY", "price": "79000", "qty": "0.05",
                                    "realizedPnl": "0", "commission": "1.58", "commissionAsset": "USDT", "time": 1794963600000}]}
        self.calls = []
        self.posrisk = POS
        self.algos = ALGOS

    def get(self, path, params=None, signed=True):
        self.calls.append(path)
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        if path == "/fapi/v1/symbolConfig":
            return [{"symbol": "BTCUSDT", "marginType": "ISOLATED", "leverage": 3}]
        raise AssertionError(path)

    def sync_clock(self):
        return 12.0

    def exchange_info(self):
        return INFO

    def premium_index(self):
        return PREMIUM

    def account(self):
        return {"totalWalletBalance": "10000", "totalMarginBalance": "10050", "availableBalance": "8600", "totalUnrealizedProfit": "50"}

    def position_risk(self):
        return self.posrisk

    def open_orders(self):
        return []

    def open_algo_orders(self):
        return self.algos

    def user_trades(self, symbol, from_id=None, start_ms=None):
        self.calls.append(("trades", symbol, from_id, start_ms is not None))
        return [t for t in self.trades.get(symbol, []) if from_id is None or t["id"] >= from_id]

    def income(self, start_ms):
        return [{"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-1.58", "asset": "USDT", "time": 1794963600000,
                 "tranId": 99, "tradeId": "1", "info": ""}]


class Lease:
    def __init__(self, token):
        self.token = token


class FakeStore:
    def __init__(self):
        self.saved = []
        self.lease = 1
        self.fill_ids = {}

    def acquire_lease(self, owner, now, ttl):
        return Lease(self.lease) if self.lease else None

    def load_risk(self):
        return None

    def last_fill_ids(self):
        return self.fill_ids

    def last_income_at(self):
        return None

    def save(self, snap, risk, body, fills, income):
        self.saved.append((snap, risk, body, fills, income))
        for f in fills:
            self.fill_ids[f["symbol"]] = max(self.fill_ids.get(f["symbol"], 0), f["trade_id"])
        return len(fills), len(income)


class Alerts:
    def __init__(self):
        self.items = []

    def push(self, severity, key, text):
        self.items.append((severity.value, key, text))

    def flush(self):
        return 0


def make_worker(rest=None, store=None):
    clock = [0.0]
    w = FuturesWorker(rest or FakeRest(), store or FakeStore(), Alerts(), Config(owner="vm:1"), now=lambda: T0,
                      monotonic=lambda: clock[0], log=lambda m: None)
    return w, clock


def test_worker_sync_writes_snapshot_fills_income_and_reads_only():
    w, clock = make_worker()
    w.start()
    w.tick()
    snap, risk, body, fills, income = w.store.saved[0]
    assert snap.margin_balance == 10050 and snap.positions[0]["leverage"] == 3 and snap.open_orders[0]["kind"] == "SL"
    assert risk.hwm == 10050 and [f["trade_id"] for f in fills] == [1] and income[0]["tran_id"] == 99
    assert body["orders_enabled"] is False and w.state.clock_offset_ms == 12.0
    # Second sync asks only for newer fills.
    clock[0] = 11
    w.tick()
    assert ("trades", "BTCUSDT", 2, False) in w.rest.calls and len(w.store.saved) == 2
    assert not [a for a in w.alerts.items if a[1].startswith("fut-nosl")]


def test_worker_alerts_once_for_position_without_stop():
    rest = FakeRest()
    rest.algos = []
    w, clock = make_worker(rest)
    w.start()
    w.tick()
    clock[0] = 11
    w.tick()
    assert [a[1] for a in w.alerts.items if a[1].startswith("fut-nosl")] == ["fut-nosl-BTCUSDT"]


def test_worker_keeps_running_after_a_failed_sync_and_stops_on_lost_lease():
    rest = FakeRest()
    rest.account = lambda: (_ for _ in ()).throw(ConnectionError("down"))
    w, clock = make_worker(rest)
    w.start()
    w.tick()
    assert w.state.last_error.startswith("ConnectionError") and not w.store.saved
    assert [a[1] for a in w.alerts.items].count("fut-sync") == 1
    w.store.lease = 0
    with pytest.raises(LeaseLost):
        w.tick()


def test_fill_row_parses_binance_fields():
    r = fill_row(FakeRest().trades["BTCUSDT"][0])
    assert r["trade_id"] == 1 and r["order_id"] == 5 and r["commission"] == Decimal("1.58") and r["traded_at"].tzinfo is UTC


# --- API view ---------------------------------------------------------------

def state_row(equity="10050", day_open="10000", hwm="10100"):
    snap = snapshot(T0, FakeRest().account() | {"totalMarginBalance": equity}, POS, [], ALGOS, markets(INFO, PREMIUM),
                    {"BTCUSDT": {"marginType": "ISOLATED", "leverage": 3}})
    return ("FUT1", T0, snap.wallet_balance, snap.margin_balance, snap.available_balance, snap.unrealized_pnl,
            snap.positions, snap.open_orders, snap.markets, {"orders_enabled": False, "host": "vm"},
            date(2026, 11, 18), Decimal(day_open), Decimal(hwm), T0, Decimal("10000"), T0)


def test_desk_view_shapes_positions_caps_and_trades():
    fills = [F(1, "BUY", "0.02", "80000", at=0), F(2, "SELL", "0.02", "81000", pnl="20", at=5), F(3, "BUY", "0.05", "79000", at=10)]
    v = desk_view(state_row(), fills, [], ["FUT1"], now=T0 + timedelta(seconds=20))
    assert v["available"] and not v["stale"] and v["equity"] == "10050" and v["unprotected"] == []
    p = v["positions"][0]
    assert p["stop_price"] == "77000" and p["initial_risk_pct"] == "0.01" and p["has_stop"] and p["stop_before_liq"]
    assert v["caps"]["day"]["level"] == "OK" and v["caps"]["total"]["hwm"] == "10100"
    assert [t["open"] for t in v["trades"]] == [True, False]          # newest first
    assert v["trades"][1]["net_pnl"] == "19" and v["summary"]["wins"] == 1 and v["summary"]["win_rate"] == "1"
    assert v["rules"]["risk_per_trade"] == "0.01"
    stale = desk_view(state_row(equity="9690"), [], [], ["FUT1"], now=T0 + timedelta(minutes=5))
    assert stale["stale"] and stale["caps"]["blocked"]


class Verifier:
    def verify(self, token):
        try:
            return UUID(token)
        except ValueError as e:
            raise AuthError("invalid_token", "invalid") from e


class Roles:
    async def active_roles(self, user):
        return frozenset({OperatorRole.VIEWER})


class Store:
    def __init__(self, rows):
        self.rows = rows

    async def state_rows(self):
        if self.rows is None:
            raise Unavailable
        return self.rows

    async def history(self, account, since):
        return [F(1, "BUY", "0.05", "79000")], []

    async def requests(self, account):
        return []


@pytest.mark.parametrize(("rows", "reason"), [(None, "no_tables"), ([], "no_worker")])
def test_desk_endpoint_without_data(rows, reason):
    app = create_app(Settings(), Verifier(), Roles(), futures=Store(rows))
    r = TestClient(app).get("/api/v1/futures/desk", headers={"Authorization": f"Bearer {UUID(int=1)}"})
    assert r.status_code == 200 and r.json() == {"available": False, "reason": reason, "accounts": []}


def test_desk_endpoint_needs_sign_in_and_returns_view():
    app = create_app(Settings(), Verifier(), Roles(), futures=Store([state_row()]))
    c = TestClient(app)
    assert c.get("/api/v1/futures/desk").status_code == 401
    r = c.get("/api/v1/futures/desk?account=FUT1", headers={"Authorization": f"Bearer {UUID(int=1)}"})
    assert r.status_code == 200 and r.json()["account"] == "FUT1" and r.json()["trades"][0]["open"] is True
