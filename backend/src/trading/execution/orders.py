"""Order state machine keyed by client order id (BUILDPLAN v4.2 §13.1).

The order keeps every fill it has seen, keyed by execution id, so a duplicate
fill event changes nothing. Cumulative filled quantity never goes down: a
stale event that reports less than what is already known is ignored. A
terminal status never removes fills, so CANCELED or EXPIRED may carry a filled
quantity that still has to be managed as a position (§13.2 example).
UNKNOWN (no answer after a submit) can only move to RECONCILING; while the
facts stay unclear the order stays there instead of being forced terminal.
An intent that is never sent ends EXPIRED (TTL passed, or its claim belonged
to a stopped writer before it sent anything) or REJECTED (refused by the
pre-trade check). SUBMITTING may end REJECTED when the exchange answers the
submit with a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading.execution.intent import OrderIntent

ZERO = Decimal(0)


class OrderState(StrEnum):
    INTENT = "INTENT"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


S = OrderState
_RESOLVED = {S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED}
TERMINAL = frozenset({S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED})
TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    S.INTENT: frozenset({S.SUBMITTING, S.EXPIRED, S.REJECTED}),
    S.SUBMITTING: frozenset({S.ACKNOWLEDGED, S.UNKNOWN, S.REJECTED}),
    S.ACKNOWLEDGED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED}),
    S.PARTIALLY_FILLED: frozenset({S.FILLED, S.CANCELED, S.EXPIRED}),
    S.UNKNOWN: frozenset({S.RECONCILING}),
    S.RECONCILING: frozenset(_RESOLVED),
}


class IllegalTransition(ValueError):
    pass


@dataclass(frozen=True)
class Fill:
    exec_id: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    at: datetime


@dataclass
class Order:
    intent: OrderIntent
    state: OrderState = OrderState.INTENT
    exchange_order_id: str | None = None
    fills: dict[str, Fill] = field(default_factory=dict)
    reported_cum_qty: Decimal = ZERO          # the largest cumulative quantity the exchange has reported
    history: list[tuple[OrderState, str]] = field(default_factory=list)

    @property
    def client_order_id(self) -> str:
        return self.intent.client_order_id

    @property
    def filled_qty(self) -> Decimal:
        """Quantity known to be filled: the larger of our fills and the exchange's cumulative report."""
        return max(sum((f.qty for f in self.fills.values()), ZERO), self.reported_cum_qty)

    @property
    def remaining_qty(self) -> Decimal:
        return ZERO if self.state in TERMINAL else self.intent.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def is_open(self) -> bool:
        """Still able to fill: counts against active-order limits and reservations."""
        return not self.is_terminal and self.state is not S.INTENT

    def transition(self, new: OrderState, reason: str = "") -> None:
        if new is self.state:
            return
        if new not in TRANSITIONS.get(self.state, frozenset()):
            raise IllegalTransition(f"{self.client_order_id}: {self.state} -> {new}")
        self.state = new
        self.history.append((new, reason))

    def add_fill(self, fill: Fill) -> bool:
        """Record a fill once; returns False for a duplicate execution id."""
        if fill.exec_id in self.fills:
            return False
        if fill.qty <= 0 or fill.price <= 0 or fill.fee < 0:
            raise ValueError("invalid fill")
        if self.filled_qty_from_fills + fill.qty > self.intent.qty:
            raise ValueError(f"{self.client_order_id}: fills exceed the order quantity")
        self.fills[fill.exec_id] = fill
        return True

    @property
    def filled_qty_from_fills(self) -> Decimal:
        return sum((f.qty for f in self.fills.values()), ZERO)

    def apply_report(self, status: OrderState, cum_qty: Decimal, exchange_order_id: str | None = None) -> bool:
        """Apply an exchange status report. Returns False when it is stale and ignored.

        A report whose cumulative quantity is below what is already known is
        stale (out-of-order delivery) and changes nothing. A report for a
        terminal order is accepted only if it raises the cumulative quantity
        (a late fill on a canceled order), and the state stays terminal.
        """
        if cum_qty < 0 or cum_qty > self.intent.qty:
            raise ValueError("cumulative quantity out of range")
        if cum_qty < self.reported_cum_qty:
            return False
        if exchange_order_id:
            self.exchange_order_id = exchange_order_id
        self.reported_cum_qty = cum_qty
        if self.is_terminal:
            return True
        target = status
        if target is S.ACKNOWLEDGED and cum_qty > 0:
            target = S.PARTIALLY_FILLED
        if self.state is S.PARTIALLY_FILLED and target is S.ACKNOWLEDGED:
            return True
        if self.state is S.UNKNOWN:
            self.transition(S.RECONCILING, "report after unknown")
        if self.state is S.SUBMITTING and target is not S.ACKNOWLEDGED:
            self.transition(S.ACKNOWLEDGED, "implied by report")
        if self.state is S.ACKNOWLEDGED and target is S.ACKNOWLEDGED:
            return True
        self.transition(target, "exchange report")
        return True
