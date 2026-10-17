"""Testnet account state from the user-data stream, checked against REST (testnet-development-plan.md §8.4; RT02, RT03, RT39).

The stream is fast but can drop events across a reconnect, so REST is the
check: on startup, after every reconnect and every 30 s the worker reads
GET /api/v3/account and openOrders and compares them with what the stream
built. A REST snapshot only counts when no stream event arrived after it was
taken; otherwise the comparison is inconclusive and runs again next time.
After a comparison the REST values become the state, and any difference is
reported, never silently merged.

Fills are kept once per (symbol, trade id), so a repeated executionReport
cannot double a fill. Open orders whose client id does not start with the
bot's prefix are someone else's (a manual Testnet order): that is a
discrepancy, because it means the bot does not own all of the account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

BOT_PREFIX = "tak-"
TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"})


@dataclass(frozen=True)
class Balance:
    free: Decimal
    locked: Decimal


@dataclass(frozen=True)
class FillEvent:
    symbol: str
    order_id: int
    client_id: str
    trade_id: int
    side: str
    qty: Decimal
    price: Decimal
    commission: Decimal
    commission_asset: str | None
    trade_time: int


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    order_id: int
    client_id: str
    status: str
    executed_qty: Decimal


class Verdict(StrEnum):
    MATCH = "MATCH"
    DISCREPANCY = "DISCREPANCY"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class Reconciliation:
    verdict: Verdict
    differences: tuple[str, ...] = ()


@dataclass
class AccountState:
    balances: dict[str, Balance] = field(default_factory=dict)
    open_orders: dict[int, OpenOrder] = field(default_factory=dict)
    fills: dict[tuple[str, int], FillEvent] = field(default_factory=dict)
    last_event_ms: int = 0
    duplicate_fills: int = 0
    stream_terminated: bool = False

    # --- stream ---------------------------------------------------------------------

    def apply(self, event: dict[str, Any], received_ms: int) -> str | None:
        """Apply one user-data event; returns its type, or None if it is not one we use."""
        kind = event.get("e")
        if kind == "outboundAccountPosition":
            for b in event.get("B", []):
                self.balances[str(b["a"])] = Balance(Decimal(b["f"]), Decimal(b["l"]))
        elif kind == "executionReport":
            self._execution(event)
        elif kind == "balanceUpdate":
            pass                     # a deposit/withdraw delta; outboundAccountPosition carries the new totals
        elif kind == "eventStreamTerminated":
            self.stream_terminated = True
        else:
            return None
        self.last_event_ms = max(self.last_event_ms, received_ms)
        return str(kind)

    def _execution(self, e: dict[str, Any]) -> None:
        symbol, oid, status = str(e["s"]), int(e["i"]), str(e["X"])
        client = str(e.get("C") or e["c"]) if e.get("x") == "CANCELED" else str(e["c"])
        trade_id = int(e.get("t", -1))
        if e.get("x") == "TRADE" and trade_id >= 0:
            key = (symbol, trade_id)
            if key in self.fills:
                self.duplicate_fills += 1
            else:
                self.fills[key] = FillEvent(symbol, oid, client, trade_id, str(e["S"]), Decimal(e["l"]), Decimal(e["L"]),
                                            Decimal(e.get("n") or 0), e.get("N"), int(e.get("T", 0)))
        if status in TERMINAL:
            self.open_orders.pop(oid, None)
        else:
            self.open_orders[oid] = OpenOrder(symbol, oid, client, status, Decimal(e["z"]))

    # --- REST check -----------------------------------------------------------------

    def reconcile(self, account: dict[str, Any], open_orders: list[dict[str, Any]], requested_ms: int,
                  first: bool = False) -> Reconciliation:
        """Compare with a REST snapshot requested at ``requested_ms``; adopt it unless a stream event came in after."""
        if not first and self.last_event_ms > requested_ms:
            return Reconciliation(Verdict.INCONCLUSIVE, ("stream events arrived during the REST read",))
        rest_bal = {str(b["asset"]): Balance(Decimal(b["free"]), Decimal(b["locked"])) for b in account.get("balances", [])}
        rest_orders = {int(o["orderId"]): OpenOrder(str(o["symbol"]), int(o["orderId"]), str(o["clientOrderId"]), str(o["status"]),
                                                    Decimal(o["executedQty"])) for o in open_orders}
        diffs: list[str] = []
        if not first:
            for asset in sorted(set(rest_bal) | set(self.balances)):
                zero = Balance(Decimal(0), Decimal(0))
                a, b = self.balances.get(asset, zero), rest_bal.get(asset, zero)
                if a != b:
                    diffs.append(f"{asset}: stream {a.free}/{a.locked}, REST {b.free}/{b.locked}")
            for oid in sorted(set(self.open_orders) - set(rest_orders)):
                diffs.append(f"order {oid} open in stream state but not on the exchange")
            for oid in sorted(set(rest_orders) - set(self.open_orders)):
                diffs.append(f"order {oid} open on the exchange but missing from stream state")
        for o in rest_orders.values():
            if not o.client_id.startswith(BOT_PREFIX):
                diffs.append(f"order {o.order_id} ({o.client_id}) was not placed by the bot")
        self.balances, self.open_orders = rest_bal, rest_orders
        return Reconciliation(Verdict.DISCREPANCY if diffs else Verdict.MATCH, tuple(diffs))
