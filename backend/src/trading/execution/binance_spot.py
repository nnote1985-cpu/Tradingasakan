"""Binance Spot Testnet adapter (testnet-development-plan.md §8.1–§8.3, P4).

Maps the core's :class:`~trading.execution.adapter.ExchangeAdapter` onto the
REST endpoints checked in P0 (docs/adr/0002): exchangeInfo, account,
order (POST/GET/DELETE), openOrders and myTrades. Every call goes through the
worker's Testnet REST client, so the endpoint guards, signing, clock offset
and Retry-After handling stay in one place.

A submit is sent once. No answer, a 5xx, or Binance's own "execution status
unknown" codes raise :class:`SubmitTimeout` and the caller reconciles by
client order id; a 4xx refusal raises :class:`OrderRejected`. A duplicate
client id raises :class:`DuplicateClientId` (an earlier attempt got through).
Only allow-listed symbols are traded; there is no margin, leverage or short.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from trading.execution.adapter import DuplicateClientId, FillReport, OrderRejected, OrderReport, SubmitTimeout, UnknownOrder
from trading.execution.intent import OrderIntent
from trading.execution.orders import OrderState
from trading.execution.rules import TradingRules
from trading.execution.types import OrderType
from trading.ingest.account import FillEvent
from trading.runtime.endpoints import RateLimited

ALLOWED_SYMBOLS = frozenset({"BTCUSDT"})
# Binance codes that mean the request may or may not have been carried out (Binance spot error codes).
UNKNOWN_CODES = frozenset({-1001, -1006, -1007})
ORDER_MISSING = -2013          # GET order: "Order does not exist."
CANCEL_UNKNOWN = -2011         # DELETE order: "Unknown order sent."
STATUS = {
    "NEW": OrderState.ACKNOWLEDGED, "PENDING_NEW": OrderState.ACKNOWLEDGED, "PENDING_CANCEL": OrderState.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED, "FILLED": OrderState.FILLED, "CANCELED": OrderState.CANCELED,
    "REJECTED": OrderState.REJECTED, "EXPIRED": OrderState.EXPIRED, "EXPIRED_IN_MATCH": OrderState.EXPIRED,
}


class Rest(Protocol):
    def call(self, method: str, path: str, params: dict[str, Any], *, signed: bool = True) -> tuple[int, Any]: ...


class TestnetError(RuntimeError):
    """A read or cancel failed in a way the caller should retry later (network, 5xx, unexpected answer)."""

    __test__ = False


def _num(d: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros, as Binance expects."""
    s = format(d.normalize(), "f")
    return s


def _err(data: Any) -> tuple[int | None, str]:
    if isinstance(data, dict):
        code = data.get("code")
        return (int(code) if isinstance(code, int | str) and str(code).lstrip("-").isdigit() else None), str(data.get("msg", ""))
    return None, ""


def _report(o: dict[str, Any], client_order_id: str | None = None) -> OrderReport:
    status = STATUS.get(str(o.get("status")))
    if status is None:
        raise TestnetError(f"unexpected order status {o.get('status')!r}")
    cid = client_order_id or str(o.get("origClientOrderId") or o.get("clientOrderId"))
    return OrderReport(cid, str(o["orderId"]), status, Decimal(str(o.get("executedQty", "0"))))


def _filters(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(f["filterType"]): f for f in info.get("filters", [])}


def rules_from_exchange_info(data: dict[str, Any], symbol: str) -> TradingRules:
    """TradingRules from exchangeInfo. max_qty is the smaller of LOT_SIZE and MARKET_LOT_SIZE (market orders use the latter)."""
    info = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
    if info is None:
        raise TestnetError(f"{symbol} is not in exchangeInfo")
    if info.get("status") != "TRADING":
        raise TestnetError(f"{symbol} status is {info.get('status')}, not TRADING")
    f = _filters(info)
    lot, price = f["LOT_SIZE"], f["PRICE_FILTER"]
    notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
    max_qty = Decimal(str(lot["maxQty"]))
    if "MARKET_LOT_SIZE" in f and Decimal(str(f["MARKET_LOT_SIZE"]["maxQty"])) > 0:
        max_qty = min(max_qty, Decimal(str(f["MARKET_LOT_SIZE"]["maxQty"])))
    return TradingRules(symbol, tick_size=Decimal(str(price["tickSize"])).normalize(), step_size=Decimal(str(lot["stepSize"])).normalize(),
                        min_qty=Decimal(str(lot["minQty"])).normalize(), max_qty=max_qty.normalize(),
                        min_notional=Decimal(str(notional.get("minNotional", "0"))).normalize())


def fill_event(symbol: str, t: dict[str, Any], client_order_id: str = "") -> FillEvent:
    """A myTrades row as the same FillEvent the user stream produces, so P3's ledger takes both."""
    return FillEvent(symbol, int(t["orderId"]), client_order_id, int(t["id"]), "BUY" if t.get("isBuyer") else "SELL",
                     Decimal(str(t["qty"])), Decimal(str(t["price"])), Decimal(str(t.get("commission") or 0)),
                     t.get("commissionAsset"), int(t["time"]))


class BinanceSpotTestnet:
    """ExchangeAdapter for Binance Spot Testnet."""

    def __init__(self, rest: Rest, symbols: Iterable[str] = ALLOWED_SYMBOLS) -> None:
        self.rest = rest
        self.symbols = frozenset(symbols)
        if not self.symbols <= ALLOWED_SYMBOLS:
            raise ValueError(f"symbols outside the allow-list: {sorted(self.symbols - ALLOWED_SYMBOLS)}")
        self._order_ids: dict[str, int] = {}

    def _symbol(self, symbol: str) -> str:
        if symbol not in self.symbols:
            raise ValueError(f"{symbol} is not an allowed Testnet symbol")
        return symbol

    def _read(self, path: str, params: dict[str, Any], *, signed: bool = True) -> Any:
        try:
            status, data = self.rest.call("GET", path, params, signed=signed)
        except (OSError, RateLimited) as e:
            raise TestnetError(f"GET {path}: {type(e).__name__}") from e
        if status != 200:
            code, msg = _err(data)
            raise TestnetError(f"GET {path}: HTTP {status} {code} {msg}".strip())
        return data

    # --- reads ------------------------------------------------------------------------

    def get_server_time(self) -> int:
        return int(self._read("/api/v3/time", {}, signed=False)["serverTime"])

    def get_trading_rules(self, symbol: str) -> TradingRules:
        return rules_from_exchange_info(self._read("/api/v3/exchangeInfo", {"symbol": self._symbol(symbol)}, signed=False), symbol)

    def get_balances(self) -> dict[str, Decimal]:
        data = self._read("/api/v3/account", {"omitZeroBalances": "true"})
        return {str(b["asset"]): Decimal(str(b["free"])) for b in data.get("balances", [])}

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderReport | None:
        try:
            status, data = self.rest.call("GET", "/api/v3/order", {"symbol": self._symbol(symbol), "origClientOrderId": client_order_id})
        except (OSError, RateLimited) as e:
            raise TestnetError(f"GET order: {type(e).__name__}") from e
        if status != 200:
            code, msg = _err(data)
            if code == ORDER_MISSING:
                return None
            raise TestnetError(f"GET order: HTTP {status} {code} {msg}")
        self._order_ids[client_order_id] = int(data["orderId"])
        return _report(data, client_order_id)

    def my_trades(self, symbol: str, *, order_id: int | None = None, from_id: int | None = None, start_ms: int | None = None,
                  end_ms: int | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": self._symbol(symbol), "limit": limit}
        if order_id is not None:
            params["orderId"] = order_id
        elif from_id is not None:
            params["fromId"] = from_id
        else:
            if start_ms is not None:
                params["startTime"] = start_ms
            if end_ms is not None:
                params["endTime"] = end_ms
        data = self._read("/api/v3/myTrades", params)
        if not isinstance(data, list):
            raise TestnetError("myTrades did not return a list")
        return data

    def list_fills(self, symbol: str, client_order_id: str) -> list[FillReport]:
        oid = self._order_ids.get(client_order_id)
        if oid is None:
            report = self.get_order_by_client_id(symbol, client_order_id)
            if report is None:
                return []
            oid = int(report.exchange_order_id)
        # exec_fills keys fills by exec_id across all accounts, and trade ids can repeat after a Testnet reset,
        # so the id carries the client order id too.
        return [FillReport(client_order_id, f"{client_order_id}:{t['id']}", Decimal(str(t["qty"])), Decimal(str(t["price"])),
                           Decimal(str(t.get("commission") or 0)), str(t.get("commissionAsset") or ""),
                           datetime.fromtimestamp(int(t["time"]) / 1000, UTC))
                for t in self.my_trades(symbol, order_id=oid)]

    def list_open_orders(self, symbol: str) -> list[OrderReport]:
        data = self._read("/api/v3/openOrders", {"symbol": self._symbol(symbol)})
        return [_report(o) for o in data]

    # --- writes -----------------------------------------------------------------------

    def submit(self, intent: OrderIntent) -> OrderReport:
        params: dict[str, Any] = {"symbol": self._symbol(intent.symbol), "side": intent.side.value, "type": intent.order_type.value,
                                  "quantity": _num(intent.qty), "newClientOrderId": intent.client_order_id, "newOrderRespType": "FULL"}
        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            params |= {"price": _num(intent.limit_price), "timeInForce": "GTC"}
        try:
            status, data = self.rest.call("POST", "/api/v3/order", params)
        except RateLimited as e:
            raise OrderRejected(None, f"rate limited: {e}") from e     # Binance refused it, or it was never sent
        except OSError as e:                                            # timeout, reset, DNS: the order may exist
            raise SubmitTimeout(type(e).__name__) from e
        if status == 200 and isinstance(data, dict) and "orderId" in data:
            self._order_ids[intent.client_order_id] = int(data["orderId"])
            return _report(data, intent.client_order_id)
        code, msg = _err(data)
        if status >= 500 or code in UNKNOWN_CODES or status == 200:
            raise SubmitTimeout(f"HTTP {status} {code} {msg}".strip())
        if "duplicate" in msg.lower():
            raise DuplicateClientId(intent.client_order_id)
        raise OrderRejected(code, msg or f"HTTP {status}")

    def cancel(self, symbol: str, client_order_id: str) -> OrderReport:
        try:
            status, data = self.rest.call("DELETE", "/api/v3/order", {"symbol": self._symbol(symbol), "origClientOrderId": client_order_id})
        except (OSError, RateLimited) as e:
            raise TestnetError(f"cancel: {type(e).__name__}") from e
        if status == 200 and isinstance(data, dict):
            return _report(data, client_order_id)
        code, msg = _err(data)
        if code == CANCEL_UNKNOWN:
            raise UnknownOrder(client_order_id)
        raise TestnetError(f"cancel: HTTP {status} {code} {msg}")
