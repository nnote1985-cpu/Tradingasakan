"""Testnet plan P4: the Spot Testnet adapter, signed calls and the conformance profile, against an in-memory Binance (§5.4, §8.1–§8.3)."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tests.fake_binance import FakeBinance
from trading.contracts.enums import TradingMode
from trading.execution import gateway
from trading.execution.adapter import DuplicateClientId, OrderRejected, SubmitTimeout, UnknownOrder
from trading.execution.binance_spot import BinanceSpotTestnet, TestnetError, fill_event
from trading.execution.intent import OrderIntent, client_order_id
from trading.execution.orders import Fill, Order, OrderState
from trading.execution.types import Side
from trading.ingest.quotes import Quote
from trading.runtime import endpoints
from trading.runtime.conformance import BOOK, LEGS, NEAR_WAIT, Conformance, cycle_id, net_base_qty
from trading.runtime.credentials import TestnetCredentials
from trading.runtime.worker import TestnetRest

T0 = datetime(2026, 10, 17, 8, 0, 5, tzinfo=UTC)


@pytest.fixture
def ex() -> FakeBinance:
    return FakeBinance()


@pytest.fixture
def adapter(ex: FakeBinance) -> BinanceSpotTestnet:
    return BinanceSpotTestnet(ex)


@pytest.fixture
def rules(adapter):
    return adapter.get_trading_rules("BTCUSDT")


def intent(rules, side=Side.BUY, qty="0.0001", price=None, leg="a") -> OrderIntent:
    return OrderIntent.build(rules, side=side, qty=D(qty), limit_price=None if price is None else D(price), mode=TradingMode.TESTNET,
                             book="t", decision_id="d", leg=leg, created_at=T0, safety_epoch=1)


# --- signed REST client --------------------------------------------------------------------


def test_call_signs_with_timestamp_and_sends_only_to_the_testnet():
    sent = []

    def send(method, url, headers):
        sent.append((method, url, headers))
        return 200, {"ok": 1}, {}

    rest = TestnetRest(TestnetCredentials("key1234", secret="s"), wall_ms=lambda: 1000.0, send=send)
    rest.offset_ms = 25
    assert rest.call("POST", "/api/v3/order", {"symbol": "BTCUSDT", "side": "BUY"}) == (200, {"ok": 1})
    method, url, headers = sent[0]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert method == "POST" and url.startswith("https://testnet.binance.vision/api/v3/order?")
    assert q["timestamp"] == ["1025"] and q["recvWindow"] == ["5000"] and "signature" in q
    assert headers == {"X-MBX-APIKEY": "key1234"}
    rest.call("GET", "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}, signed=False)
    assert "signature" not in sent[1][1] and sent[1][2] == {}


def test_429_blocks_further_calls_until_retry_after_passes():
    clock = [0.0]
    calls = []

    def send(method, url, headers):
        calls.append(url)
        return 429, {"code": -1003, "msg": "Too many requests"}, {"Retry-After": "7"}

    rest = TestnetRest(TestnetCredentials("k", secret="s"), wall_ms=lambda: clock[0], send=send)
    with pytest.raises(endpoints.RateLimited):
        rest.call("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"})
    assert rest.rate_limited
    with pytest.raises(endpoints.RateLimited, match="wait"):
        rest.call("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"})
    assert len(calls) == 1                                          # the second call was never sent
    clock[0] = 7001
    assert not rest.rate_limited


def test_request_json_refuses_other_methods():
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.request_json("PUT", "https://testnet.binance.vision/api/v3/order", {})


# --- adapter -------------------------------------------------------------------------------


def test_rules_come_from_exchange_info(rules):
    assert (rules.tick_size, rules.step_size, rules.min_qty, rules.min_notional) == (D("0.01"), D("0.00001"), D("0.00001"), D("5"))
    assert rules.max_qty == D("133.97")                             # MARKET_LOT_SIZE is smaller than LOT_SIZE
    assert rules.qty_for_notional(D("6"), D("65000")) == D("0.0001")


def test_symbols_outside_the_allow_list_are_refused(ex):
    with pytest.raises(ValueError):
        BinanceSpotTestnet(ex, symbols=("ETHUSDT",))
    with pytest.raises(ValueError):
        BinanceSpotTestnet(ex).get_order_by_client_id("ETHUSDT", "x")


def test_market_buy_fills_and_fills_are_listed(adapter, ex, rules):
    it = intent(rules)
    r = adapter.submit(it)
    assert r.status is OrderState.FILLED and r.cum_qty == D("0.0001") and r.client_order_id == it.client_order_id
    post = next(c for c in ex.calls if c[0] == "POST")
    assert post[2] | {} == {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.0001",
                             "newClientOrderId": it.client_order_id, "newOrderRespType": "FULL"}
    [f] = adapter.list_fills("BTCUSDT", it.client_order_id)
    assert f.qty == D("0.0001") and f.price == D("65000.01") and f.fee_asset == "BNB" and f.exec_id.startswith(it.client_order_id + ":")


def test_limit_price_and_quantity_are_plain_text(adapter, ex, rules):
    adapter.submit(intent(rules, qty="0.00010", price="61750.50"))
    post = next(c for c in ex.calls if c[0] == "POST")[2]
    assert post["price"] == "61750.5" and post["quantity"] == "0.0001" and post["timeInForce"] == "GTC"


@pytest.mark.parametrize("how, exc", [("timeout_lost", SubmitTimeout), ("timeout_accepted", SubmitTimeout), ("http_503", SubmitTimeout),
                                      ("reject", OrderRejected)])
def test_submit_failures_map_to_unknown_or_rejected(adapter, ex, rules, how, exc):
    ex.plan(how)
    with pytest.raises(exc):
        adapter.submit(intent(rules))


def test_duplicate_client_id_is_reported(adapter, rules):
    it = intent(rules)
    adapter.submit(it)
    with pytest.raises(DuplicateClientId):
        adapter.submit(it)


def test_query_missing_order_is_none_and_cancel_of_a_done_order_is_unknown(adapter, rules):
    assert adapter.get_order_by_client_id("BTCUSDT", "tak-nothing") is None
    it = intent(rules)
    adapter.submit(it)
    with pytest.raises(UnknownOrder):
        adapter.cancel("BTCUSDT", it.client_order_id)


def test_read_errors_raise_testnet_error(ex):
    def broken(*_a, **_k):
        raise ConnectionResetError()

    ex.call = broken  # type: ignore[method-assign]
    with pytest.raises(TestnetError):
        BinanceSpotTestnet(ex).list_open_orders("BTCUSDT")


def test_my_trades_rows_become_ledger_fill_events(adapter, ex, rules):
    adapter.submit(intent(rules, side=Side.SELL))
    [t] = adapter.my_trades("BTCUSDT", from_id=0)
    f = fill_event("BTCUSDT", t, "tak-x")
    assert (f.side, f.qty, f.price, f.client_id) == ("SELL", D("0.0001"), D("65000"), "tak-x")


# --- gateway over the adapter -----------------------------------------------------------------


def test_exchange_refusal_ends_rejected(adapter, ex, rules):
    ex.plan("reject")
    o = Order(intent(rules))
    assert gateway.submit(o, adapter) is OrderState.REJECTED
    assert "insufficient balance" in o.history[-1][1]


def test_timeout_after_accept_is_found_by_client_id(adapter, ex, rules):
    ex.plan("timeout_accepted")
    o = Order(intent(rules))
    written = []
    assert gateway.submit(o, adapter, before_send=lambda x: written.append(x.state)) is OrderState.UNKNOWN
    assert written == [OrderState.SUBMITTING]
    assert gateway.reconcile(o, adapter) is gateway.ReconcileResult.RESOLVED
    assert o.state is OrderState.FILLED and len(o.fills) == 1


def test_timeout_lost_stays_reconciling_not_rejected(adapter, ex, rules):
    ex.plan("timeout_lost")
    o = Order(intent(rules))
    gateway.submit(o, adapter)
    assert gateway.reconcile(o, adapter) is gateway.ReconcileResult.NOT_FOUND
    assert o.state is OrderState.RECONCILING and ex.orders == {}


def test_cancel_keeps_a_partial_fill(adapter, ex, rules):
    o = Order(intent(rules, qty="0.0002", price="64000"))
    gateway.submit(o, adapter)
    ex.fill(o.client_order_id, D("0.0001"))
    gateway.cancel(o, adapter)
    assert o.state is OrderState.CANCELED and o.filled_qty == D("0.0001") and len(o.fills) == 1


def test_cancel_racing_a_full_fill_falls_back_to_a_query(adapter, ex, rules):
    o = Order(intent(rules, price="64000"))
    gateway.submit(o, adapter)
    ex.fill(o.client_order_id)
    gateway.cancel(o, adapter)
    assert o.state is OrderState.FILLED and o.filled_qty == D("0.0001")


# --- conformance profile ------------------------------------------------------------------------


def quote(bid="65000", ask="65000.01") -> Quote:
    return Quote("BTCUSDT", D(bid), D("1"), D(ask), D("1"), 1, 0)


class Book:
    """Orders the profile has made, driven by hand: each step's intent becomes an order in the state we set."""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}

    def lookup(self, cid: str) -> Order | None:
        return self.orders.get(cid)

    def add(self, it: OrderIntent, state: OrderState, filled: str = "0", fee_btc: str = "0") -> Order:
        o = Order(it)
        o.state = state
        if D(filled) > 0:
            o.fills["x" + it.leg] = Fill("x" + it.leg, D(filled), D("65000"), D(fee_btc) or D("0.00001"), "BTC" if D(fee_btc) else "BNB", T0)
            o.reported_cum_qty = D(filled)
        self.orders[it.client_order_id] = o
        return o


def test_cycle_ids_are_4_hour_utc_slots():
    assert cycle_id(T0) == "20261017T0800Z"
    assert cycle_id(T0 + timedelta(hours=3, minutes=59)) == "20261017T0800Z"
    assert cycle_id(T0 + timedelta(hours=4)) == "20261017T1200Z"


def test_conformance_runs_the_legs_in_order_and_sells_back_what_it_got(rules):
    c, book, now = Conformance(), Book(), T0
    step = lambda: c.step(now=now, rules=rules, quote=quote(), safety_epoch=1, lookup=book.lookup, open_orders=list(book.orders.values()))  # noqa: E731

    s = step()
    [(buy, price)] = s.intents
    assert buy.leg == "mkt-buy" and buy.side is Side.BUY and buy.limit_price is None and price == D("65000.01")
    assert buy.qty * price >= rules.min_notional * D("1.2")
    assert buy.client_order_id == client_order_id(BOOK, "20261017T0800Z", "mkt-buy")
    assert step().intents and len(step().intents) == 1               # the same intent again: the store refuses a second one
    book.add(buy, OrderState.FILLED, filled=str(buy.qty), fee_btc="0.0000001")

    [(sell, _)] = step().intents
    assert sell.leg == "mkt-sell" and sell.side is Side.SELL and sell.qty == rules.round_qty(buy.qty - D("0.0000001"))
    book.add(sell, OrderState.FILLED, filled=str(sell.qty))

    [(far, far_price)] = step().intents
    assert far.leg == "limit-far" and far_price == D("61750.00")
    o = book.add(far, OrderState.ACKNOWLEDGED)
    assert step().cancels == [far.client_order_id]
    assert step().cancels == []                                       # not re-sent within 30 s
    o.state = OrderState.CANCELED

    [(near, near_price)] = step().intents
    assert near.leg == "limit-near" and near_price == D("65000.00")
    o = book.add(near, OrderState.PARTIALLY_FILLED, filled="0.00005")
    assert step().cancels == []                                        # waits up to 10 minutes for more
    now = T0 + NEAR_WAIT
    assert step().cancels == [near.client_order_id]
    o.state = OrderState.CANCELED
    s = step()                                                         # 0.00005 BTC is below min notional: left as dust
    assert s.intents == [] and c.cycles_done == 1 and c.last_note.endswith("done")
    assert step().intents == [] and c.cycles_done == 1
    assert [leg for leg in LEGS] == ["mkt-buy", "mkt-sell", "limit-far", "limit-near", "near-sell"]


def test_conformance_skips_the_sell_when_the_buy_got_nothing(rules):
    c, book = Conformance(), Book()
    [(buy, _)] = c.step(now=T0, rules=rules, quote=quote(), safety_epoch=1, lookup=book.lookup, open_orders=[]).intents
    book.add(buy, OrderState.REJECTED)
    [(nxt, _)] = c.step(now=T0, rules=rules, quote=quote(), safety_epoch=1, lookup=book.lookup, open_orders=[]).intents
    assert nxt.leg == "limit-far"


def test_conformance_waits_for_a_quote_and_cancels_old_cycle_limits(rules):
    c, book = Conformance(), Book()
    s = c.step(now=T0, rules=rules, quote=None, safety_epoch=1, lookup=book.lookup, open_orders=[])
    assert s.intents == [] and "quote" in s.note
    old = OrderIntent.build(rules, side=Side.BUY, qty=D("0.0001"), limit_price=D("64000"), mode=TradingMode.TESTNET, book=BOOK,
                            decision_id="20261017T0400Z", leg="limit-near", created_at=T0 - timedelta(hours=4), safety_epoch=1)
    o = book.add(old, OrderState.ACKNOWLEDGED)
    s = c.step(now=T0, rules=rules, quote=quote(), safety_epoch=1, lookup=book.lookup, open_orders=[o])
    assert s.cancels == [o.client_order_id] and s.intents[0][0].leg == "mkt-buy"


def test_net_base_qty_waits_for_missing_fills(rules):
    o = Order(intent(rules))
    o.reported_cum_qty = D("0.0001")
    assert net_base_qty(o, "BTC") is None
