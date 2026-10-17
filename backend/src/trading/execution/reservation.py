"""Account-wide risk reservations (BUILDPLAN v4.2 §12.2).

A buy reserves its notional (quote currency) when the intent is accepted.
Filled quantity turns into position exposure; only quantity confirmed
unfilled (the order is terminal) is released. An UNKNOWN or open order keeps
its whole remaining reservation, so a timeout can never free budget that the
exchange may still be using.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading.execution.orders import Order

ZERO = Decimal(0)


class InsufficientBudget(ValueError):
    pass


@dataclass
class ReservationBook:
    budget: Decimal                                   # quote currency the account may commit to buys
    _reserved: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)   # client id -> (qty, unit price)

    def reserved(self) -> Decimal:
        return sum((q * p for q, p in self._reserved.values()), ZERO)

    def available(self) -> Decimal:
        return self.budget - self.reserved()

    def reserve(self, client_order_id: str, qty: Decimal, unit_price: Decimal) -> None:
        if client_order_id in self._reserved:
            return                                    # same intent again: nothing new to reserve
        need = qty * unit_price
        if need > self.available():
            raise InsufficientBudget(f"needs {need}, available {self.available()}")
        self._reserved[client_order_id] = (qty, unit_price)

    def sync(self, order: Order) -> None:
        """Shrink the reservation to the order's unfilled remainder; drop it once terminal.

        Filled quantity leaves the reservation and becomes position exposure
        (the caller lowers ``budget`` by the fill cost from the ledger).
        """
        cid = order.client_order_id
        if cid not in self._reserved:
            return
        if order.is_terminal:
            del self._reserved[cid]
            return
        _, price = self._reserved[cid]
        self._reserved[cid] = (order.intent.qty - order.filled_qty, price)
