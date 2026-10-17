"""An in-memory Binance Spot Testnet that answers like the REST API (no network), for the P4 adapter tests.

It stands in for ``TestnetRest.call``: same arguments, same (status, JSON) answers,
same error codes. MARKET orders fill at once at the ask (buy) or bid (sell);
LIMIT orders rest until ``fill`` is called. ``plan`` scripts the next submit:

- ``timeout_accepted``: the order is placed but the call raises a timeout;
- ``timeout_lost``: the call raises a timeout and nothing is placed;
- ``http_503``: a 5xx answer, nothing placed;
- ``reject``: -2010 insufficient balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

EXCHANGE_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000.00", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": "0.00001000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000", "applyMinToMarket": True},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "133.97000000", "stepSize": "0.00000000"},
        {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
    ]}]}
OPEN = ("NEW", "PARTIALLY_FILLED")


@dataclass
class _O:
    order_id: int
    client_id: str
    side: str
    type: str
    qty: Decimal
    price: Decimal | None
    status: str = "NEW"
    executed: Decimal = Decimal(0)

    def json(self) -> dict[str, Any]:
        return {"symbol": "BTCUSDT", "orderId": self.order_id, "clientOrderId": self.client_id, "status": self.status,
                "origQty": str(self.qty), "executedQty": str(self.executed), "type": self.type, "side": self.side,
                "price": str(self.price or 0)}


@dataclass
class FakeBinance:
    bid: Decimal = Decimal("65000")
    ask: Decimal = Decimal("65000.01")
    fee_rate: Decimal = Decimal("0.001")
    fee_asset: str = "BNB"                   # BNB unless set; "BASE" charges a buy in BTC
    now_ms: int = 1_792_224_000_000
    balances: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(1), "USDT": Decimal(10000), "BNB": Decimal(1)})
    orders: dict[str, _O] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    script: list[str] = field(default_factory=list)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    _oid: int = 100
    _tid: int = 5000

    def plan(self, *behaviours: str) -> None:
        self.script.extend(behaviours)

    def _fill(self, o: _O, qty: Decimal, price: Decimal) -> None:
        self._tid += 1
        quote = qty * price
        if self.fee_asset == "BASE":
            asset, fee = ("BTC", qty * self.fee_rate) if o.side == "BUY" else ("USDT", quote * self.fee_rate)
        else:
            asset, fee = self.fee_asset, Decimal("0.00001")
        self.trades.append({"symbol": "BTCUSDT", "id": self._tid, "orderId": o.order_id, "price": str(price), "qty": str(qty),
                            "quoteQty": str(quote), "commission": str(fee), "commissionAsset": asset, "time": self.now_ms,
                            "isBuyer": o.side == "BUY", "isMaker": o.type == "LIMIT"})
        sign = 1 if o.side == "BUY" else -1
        self.balances["BTC"] += sign * qty
        self.balances["USDT"] -= sign * quote
        self.balances[asset] = self.balances.get(asset, Decimal(0)) - fee
        o.executed += qty
        o.status = "FILLED" if o.executed == o.qty else "PARTIALLY_FILLED"

    def fill(self, client_id: str, qty: Decimal | None = None) -> None:
        """Match a resting LIMIT order (fully, or ``qty`` of it) at its own price."""
        o = self.orders[client_id]
        assert o.price is not None
        self._fill(o, qty if qty is not None else o.qty - o.executed, o.price)

    def manual_order(self, client_id: str = "web_manual_1") -> None:
        """An order placed outside the bot (e.g. on the Testnet website)."""
        self._oid += 1
        self.orders[client_id] = _O(self._oid, client_id, "BUY", "LIMIT", Decimal("0.001"), Decimal("1000"))

    def call(self, method: str, path: str, params: dict[str, Any], *, signed: bool = True) -> tuple[int, Any]:
        self.calls.append((method, path, dict(params)))
        p = params
        if path == "/api/v3/exchangeInfo":
            return 200, EXCHANGE_INFO
        if path == "/api/v3/time":
            return 200, {"serverTime": self.now_ms}
        if path == "/api/v3/account":
            return 200, {"canTrade": True, "balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items()]}
        if path == "/api/v3/openOrders":
            return 200, [o.json() for o in self.orders.values() if o.status in OPEN]
        if path == "/api/v3/myTrades":
            rows = [t for t in self.trades if "orderId" not in p or t["orderId"] == p["orderId"]]
            if "fromId" in p:
                rows = [t for t in rows if t["id"] >= p["fromId"]]
            if "startTime" in p:
                rows = [t for t in rows if p["startTime"] <= t["time"] <= p.get("endTime", p["startTime"] + 86_400_000)]
            return 200, rows[: p.get("limit", 500)]
        if path == "/api/v3/order" and method == "GET":
            o = self.orders.get(p["origClientOrderId"])
            return (200, o.json()) if o else (400, {"code": -2013, "msg": "Order does not exist."})
        if path == "/api/v3/order" and method == "DELETE":
            o = self.orders.get(p["origClientOrderId"])
            if o is None or o.status not in OPEN:
                return 400, {"code": -2011, "msg": "Unknown order sent."}
            o.status = "CANCELED"
            return 200, o.json() | {"origClientOrderId": o.client_id, "clientOrderId": "cancel-xyz"}
        if path == "/api/v3/order" and method == "POST":
            return self._submit(p)
        raise AssertionError(f"unexpected call {method} {path}")

    def _submit(self, p: dict[str, Any]) -> tuple[int, Any]:
        how = self.script.pop(0) if self.script else "ok"
        if how == "timeout_lost":
            raise TimeoutError("timed out")
        if how == "http_503":
            return 503, {"code": -1001, "msg": "Internal error; unable to process your request."}
        if how == "reject":
            return 400, {"code": -2010, "msg": "Account has insufficient balance for requested action."}
        cid = p["newClientOrderId"]
        if cid in self.orders:
            return 400, {"code": -2010, "msg": "Duplicate order sent."}
        self._oid += 1
        o = _O(self._oid, cid, p["side"], p["type"], Decimal(p["quantity"]), Decimal(p["price"]) if "price" in p else None)
        self.orders[cid] = o
        if o.type == "MARKET":
            self._fill(o, o.qty, self.ask if o.side == "BUY" else self.bid)
        if how == "timeout_accepted":
            raise TimeoutError("timed out")
        fills = [{"price": t["price"], "qty": t["qty"], "commission": t["commission"], "commissionAsset": t["commissionAsset"],
                  "tradeId": t["id"]} for t in self.trades if t["orderId"] == o.order_id]
        return 200, o.json() | {"fills": fills, "transactTime": self.now_ms}
