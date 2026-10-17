"""The exchange interface our core owns (BUILDPLAN v4.2 §12.1), spot subset.

Adapters translate units, rounding and flags; the core never builds endpoints.
A submit that gets no answer raises :class:`SubmitTimeout`: the exchange may
or may not have accepted it, so the caller marks the order UNKNOWN and
reconciles before anything else. Futures methods (stop coverage, reduce-only)
join this interface with the futures adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trading.execution.intent import OrderIntent
from trading.execution.orders import OrderState
from trading.execution.rules import TradingRules


class SubmitTimeout(Exception):
    """No answer to a submit; the outcome is unknown."""


class DuplicateClientId(Exception):
    """The exchange already has an order with this client id."""


class OrderRejected(Exception):
    """The exchange answered the submit with a refusal; the order was not placed."""

    def __init__(self, code: int | None, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code, self.msg = code, msg


class UnknownOrder(Exception):
    """A cancel found no open order with this client id (already filled, canceled, or never placed)."""


@dataclass(frozen=True)
class OrderReport:
    client_order_id: str
    exchange_order_id: str
    status: OrderState
    cum_qty: Decimal


@dataclass(frozen=True)
class FillReport:
    client_order_id: str
    exec_id: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    at: datetime


class ExchangeAdapter(Protocol):
    def get_trading_rules(self, symbol: str) -> TradingRules: ...
    def get_balances(self) -> dict[str, Decimal]: ...
    def submit(self, intent: OrderIntent) -> OrderReport: ...
    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderReport | None: ...
    def list_fills(self, symbol: str, client_order_id: str) -> list[FillReport]: ...
    def cancel(self, symbol: str, client_order_id: str) -> OrderReport: ...
    def list_open_orders(self, symbol: str) -> list[OrderReport]: ...
