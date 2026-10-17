"""A scripted in-memory exchange for tests (no network).

Each submit follows the next scripted behaviour:

- ``ACK``: accepted, nothing filled.
- ``FILL``: accepted and fully filled at the mark.
- ``PARTIAL``: accepted and ``partial_qty`` filled.
- ``REJECT``: refused.
- ``TIMEOUT_ACCEPTED``: accepted (and filled per ``then``), but the caller gets a timeout.
- ``TIMEOUT_LOST``: never reached the exchange; the caller gets a timeout.

A client id it has seen before raises :class:`DuplicateClientId`, like Binance.
Fill listings can be made to repeat an execution (duplicate delivery).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from trading.execution.adapter import DuplicateClientId, FillReport, OrderReport, SubmitTimeout
from trading.execution.intent import OrderIntent
from trading.execution.orders import TERMINAL, OrderState
from trading.execution.rules import TradingRules
from trading.execution.types import Side

ZERO = Decimal(0)


class Behaviour(StrEnum):
    ACK = "ACK"
    FILL = "FILL"
    PARTIAL = "PARTIAL"
    REJECT = "REJECT"
    TIMEOUT_ACCEPTED = "TIMEOUT_ACCEPTED"
    TIMEOUT_LOST = "TIMEOUT_LOST"


@dataclass
class _Order:
    intent: OrderIntent
    exchange_id: str
    status: OrderState
    fills: list[FillReport] = field(default_factory=list)

    @property
    def cum(self) -> Decimal:
        return sum((f.qty for f in self.fills), ZERO)

    def report(self) -> OrderReport:
        return OrderReport(self.intent.client_order_id, self.exchange_id, self.status, self.cum)


@dataclass
class FakeExchange:
    rules: TradingRules
    mark: Decimal
    balances: dict[str, Decimal] = field(default_factory=lambda: {"USDT": Decimal("10000"), "BTC": ZERO})
    fee_rate: Decimal = Decimal("0.001")
    script: list[tuple[Behaviour, Behaviour | None]] = field(default_factory=list)
    partial_qty: Decimal = ZERO
    repeat_fills: bool = False
    orders: dict[str, _Order] = field(default_factory=dict)
    submits: int = 0
    _seq: int = 0

    def plan(self, behaviour: Behaviour, then: Behaviour | None = None) -> None:
        self.script.append((behaviour, then))

    def get_trading_rules(self, symbol: str) -> TradingRules:
        return self.rules

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self.balances)

    def _fill(self, o: _Order, qty: Decimal) -> None:
        self._seq += 1
        price = self.mark
        fee = qty * price * self.fee_rate
        o.fills.append(FillReport(o.intent.client_order_id, f"x{self._seq}", qty, price, fee, "USDT", datetime.now(UTC)))
        base = self.rules.symbol.removesuffix("USDT")
        sign = 1 if o.intent.side is Side.BUY else -1
        self.balances[base] = self.balances.get(base, ZERO) + sign * qty
        self.balances["USDT"] -= sign * qty * price + fee
        o.status = OrderState.FILLED if o.cum == o.intent.qty else OrderState.PARTIALLY_FILLED

    def _accept(self, intent: OrderIntent, how: Behaviour) -> _Order:
        self._seq += 1
        o = _Order(intent, f"e{self._seq}", OrderState.ACKNOWLEDGED)
        self.orders[intent.client_order_id] = o
        if how is Behaviour.FILL:
            self._fill(o, intent.qty)
        elif how is Behaviour.PARTIAL:
            self._fill(o, self.partial_qty)
        elif how is Behaviour.REJECT:
            o.status = OrderState.REJECTED
        return o

    def submit(self, intent: OrderIntent) -> OrderReport:
        self.submits += 1
        if intent.client_order_id in self.orders:
            raise DuplicateClientId(intent.client_order_id)
        how, then = self.script.pop(0) if self.script else (Behaviour.ACK, None)
        if how is Behaviour.TIMEOUT_LOST:
            raise SubmitTimeout()
        if how is Behaviour.TIMEOUT_ACCEPTED:
            self._accept(intent, then or Behaviour.ACK)
            raise SubmitTimeout()
        return self._accept(intent, how).report()

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderReport | None:
        o = self.orders.get(client_order_id)
        return None if o is None else o.report()

    def list_fills(self, symbol: str, client_order_id: str) -> list[FillReport]:
        o = self.orders.get(client_order_id)
        if o is None:
            return []
        return o.fills + o.fills if self.repeat_fills else list(o.fills)

    def cancel(self, symbol: str, client_order_id: str) -> OrderReport:
        o = self.orders[client_order_id]
        if o.status not in TERMINAL:
            o.status = OrderState.CANCELED
        return o.report()

    def late_fill(self, client_order_id: str, qty: Decimal) -> None:
        """A fill the exchange matched just before a cancel took effect."""
        o = self.orders[client_order_id]
        status = o.status
        self._fill(o, qty)
        if status in TERMINAL:
            o.status = status

    def list_open_orders(self, symbol: str) -> list[OrderReport]:
        return [o.report() for o in self.orders.values() if o.status not in TERMINAL]
