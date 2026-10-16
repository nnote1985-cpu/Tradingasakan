"""Submit and reconcile one order through an adapter (BUILDPLAN v4.2 §12.2–§12.3).

``submit`` runs once per order: the state machine refuses a second submit, and
the client order id never changes. A timeout leaves the order UNKNOWN.
``reconcile`` asks the exchange by client id and applies what it finds. Not
finding the order once is not a rejection: the order stays RECONCILING and the
caller keeps entries blocked and escalates (§12.3 step 5).
"""

from __future__ import annotations

from enum import StrEnum

from trading.execution.adapter import DuplicateClientId, ExchangeAdapter, SubmitTimeout
from trading.execution.orders import Fill, Order, OrderState


class ReconcileResult(StrEnum):
    RESOLVED = "RESOLVED"          # the exchange answered; state and fills applied
    NOT_FOUND = "NOT_FOUND"        # still unknown; keep blocking and escalate


def submit(order: Order, adapter: ExchangeAdapter) -> OrderState:
    order.transition(OrderState.SUBMITTING, "submit")
    try:
        report = adapter.submit(order.intent)
    except (SubmitTimeout, DuplicateClientId) as e:
        # A duplicate id means an earlier attempt reached the exchange; treat it like a timeout and reconcile.
        order.transition(OrderState.UNKNOWN, type(e).__name__)
        return order.state
    order.apply_report(report.status, report.cum_qty, report.exchange_order_id)
    return order.state


def apply_fills(order: Order, adapter: ExchangeAdapter) -> int:
    """Pull fills for the order; returns how many were new (duplicates are ignored)."""
    new = 0
    for f in adapter.list_fills(order.intent.symbol, order.client_order_id):
        if f.client_order_id != order.client_order_id:
            continue
        new += order.add_fill(Fill(f.exec_id, f.qty, f.price, f.fee, f.fee_asset, f.at))
    return new


def reconcile(order: Order, adapter: ExchangeAdapter) -> ReconcileResult:
    if order.state is OrderState.UNKNOWN:
        order.transition(OrderState.RECONCILING, "reconcile")
    report = adapter.get_order_by_client_id(order.intent.symbol, order.client_order_id)
    if report is None:
        return ReconcileResult.NOT_FOUND
    order.apply_report(report.status, report.cum_qty, report.exchange_order_id)
    apply_fills(order, adapter)
    return ReconcileResult.RESOLVED
