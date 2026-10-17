"""Phase 4 deliverable 1: order core (docs/phase-4-execution-plan.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from trading.contracts.enums import TradingMode
from trading.execution import gateway
from trading.execution.fake_exchange import Behaviour, FakeExchange
from trading.execution.intent import OrderIntent, client_order_id
from trading.execution.limits import Activity, MarketView, PreTradeLimits, check, check_cancel
from trading.execution.orders import Fill, IllegalTransition, Order, OrderState
from trading.execution.reservation import InsufficientBudget, ReservationBook
from trading.execution.rules import TradingRules
from trading.execution.safety import AccountSafety, IllegalSafetyTransition, SafetyState
from trading.execution.types import OrderType, Side

T0 = datetime(2026, 10, 16, 12, tzinfo=UTC)
RULES = TradingRules("BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"), min_qty=D("0.00001"),
                     max_qty=D("9000"), min_notional=D("5"))


def intent(side: Side = Side.BUY, qty: str = "0.069", leg: str = "1", price: str | None = None, epoch: int = 1,
           mode: TradingMode = TradingMode.TESTNET) -> OrderIntent:
    return OrderIntent.build(RULES, side=side, qty=D(qty), limit_price=None if price is None else D(price), mode=mode,
                             book="testnet/spot", decision_id="d1", leg=leg, created_at=T0, safety_epoch=epoch)


def running() -> AccountSafety:
    s = AccountSafety()
    s.recovered(T0, reconciled=True)
    return s


# --- rules -----------------------------------------------------------------

def test_rounding_never_makes_an_order_bigger_or_more_aggressive() -> None:
    assert RULES.round_qty(D("0.0691239")) == D("0.06912")
    assert RULES.round_price(D("65000.019"), Side.BUY) == D("65000.01")
    assert RULES.round_price(D("65000.011"), Side.SELL) == D("65000.02")


def test_rules_report_lot_size_and_min_notional() -> None:
    assert RULES.violations(D("0.00001"), D("65000")) == ["NOTIONAL_BELOW_MIN"]
    assert "QTY_NOT_ON_STEP" in RULES.violations(D("0.000015"), D("65000"))
    assert RULES.violations(D("0.001"), D("65000")) == []


# --- intents ---------------------------------------------------------------

def test_client_id_is_stable_and_fits_binance() -> None:
    a, b = intent(), intent()
    assert a.client_order_id == b.client_order_id == client_order_id("testnet/spot", "d1", "1")
    assert a.client_order_id != intent(leg="2").client_order_id
    assert len(a.client_order_id) <= 36 and all(c.isalnum() or c in "-_." for c in a.client_order_id)


@pytest.mark.parametrize("mode", [TradingMode.LIVE, TradingMode.SHADOW])
def test_intents_are_testnet_only(mode: TradingMode) -> None:
    with pytest.raises(ValueError, match="TESTNET only"):
        intent(mode=mode)


def test_limit_intent_is_rounded() -> None:
    i = intent(price="65000.019")
    assert i.order_type is OrderType.LIMIT and i.limit_price == D("65000.01")


# --- order state machine ---------------------------------------------------

def test_illegal_transitions_raise() -> None:
    o = Order(intent())
    with pytest.raises(IllegalTransition):
        o.transition(OrderState.FILLED)
    o.transition(OrderState.SUBMITTING)
    o.transition(OrderState.UNKNOWN)
    with pytest.raises(IllegalTransition):
        o.transition(OrderState.REJECTED)        # UNKNOWN must go through RECONCILING


def test_duplicate_fill_changes_nothing() -> None:
    o = Order(intent())
    f = Fill("x1", D("0.04"), D("65000"), D("2.6"), "USDT", T0)
    assert o.add_fill(f) is True
    assert o.add_fill(f) is False
    assert o.filled_qty == D("0.04")


def test_stale_report_never_lowers_cumulative_qty() -> None:
    o = Order(intent())
    o.transition(OrderState.SUBMITTING)
    o.apply_report(OrderState.PARTIALLY_FILLED, D("0.04"), "e1")
    assert o.apply_report(OrderState.ACKNOWLEDGED, D("0"), "e1") is False
    assert o.state is OrderState.PARTIALLY_FILLED and o.filled_qty == D("0.04")


def test_canceled_with_partial_fill_still_carries_the_fill() -> None:
    """§13.2: buy 0.069, fill 0.040, remainder canceled -> 0.040 is still a position."""
    ex = FakeExchange(RULES, mark=D("65000"), partial_qty=D("0.04"))
    ex.plan(Behaviour.PARTIAL)
    o = Order(intent())
    gateway.submit(o, ex)
    ex.cancel("BTCUSDT", o.client_order_id)
    assert gateway.reconcile(o, ex) is gateway.ReconcileResult.RESOLVED
    assert o.state is OrderState.CANCELED and o.filled_qty == D("0.04") and o.remaining_qty == 0


def test_late_fill_after_cancel_is_recorded_and_state_stays_terminal() -> None:
    ex = FakeExchange(RULES, mark=D("65000"))
    o = Order(intent())
    gateway.submit(o, ex)
    ex.cancel("BTCUSDT", o.client_order_id)
    gateway.reconcile(o, ex)
    ex.late_fill(o.client_order_id, D("0.01"))
    gateway.reconcile(o, ex)
    assert o.state is OrderState.CANCELED and o.filled_qty == D("0.01")


# --- gateway: timeouts and reconciliation (§12.3) --------------------------

def test_timeout_that_reached_the_exchange_is_journaled_once() -> None:
    ex = FakeExchange(RULES, mark=D("65000"), partial_qty=D("0.04"), repeat_fills=True)
    ex.plan(Behaviour.TIMEOUT_ACCEPTED, then=Behaviour.PARTIAL)
    o = Order(intent())
    assert gateway.submit(o, ex) is OrderState.UNKNOWN
    assert gateway.reconcile(o, ex) is gateway.ReconcileResult.RESOLVED
    assert o.state is OrderState.PARTIALLY_FILLED
    assert len(o.fills) == 1 and o.filled_qty == D("0.04")       # the repeated fill delivery was ignored
    gateway.reconcile(o, ex)
    assert len(o.fills) == 1
    assert ex.submits == 1                                          # never resubmitted


def test_lost_submit_stays_reconciling_not_rejected() -> None:
    ex = FakeExchange(RULES, mark=D("65000"))
    ex.plan(Behaviour.TIMEOUT_LOST)
    o = Order(intent())
    gateway.submit(o, ex)
    assert gateway.reconcile(o, ex) is gateway.ReconcileResult.NOT_FOUND
    assert o.state is OrderState.RECONCILING and not o.is_terminal
    with pytest.raises(IllegalTransition):
        gateway.submit(o, ex)                                       # no blind retry
    assert ex.submits == 1


def test_resubmitting_the_same_intent_is_caught_as_duplicate() -> None:
    ex = FakeExchange(RULES, mark=D("65000"))
    first = Order(intent())
    gateway.submit(first, ex)
    again = Order(intent())                                         # e.g. after a crash, same business key
    assert gateway.submit(again, ex) is OrderState.UNKNOWN
    gateway.reconcile(again, ex)
    assert again.state is OrderState.ACKNOWLEDGED and len(ex.orders) == 1


def test_reject_and_fill_paths() -> None:
    ex = FakeExchange(RULES, mark=D("65000"))
    ex.plan(Behaviour.REJECT)
    ex.plan(Behaviour.FILL)
    r, f = Order(intent(leg="r")), Order(intent(leg="f"))
    assert gateway.submit(r, ex) is OrderState.REJECTED
    assert gateway.submit(f, ex) is OrderState.FILLED
    assert gateway.apply_fills(f, ex) == 1 and f.filled_qty == D("0.069")


# --- safety state ----------------------------------------------------------

def test_restart_starts_recovering_and_needs_clean_reconciliation() -> None:
    s = AccountSafety()
    assert s.state is SafetyState.RECOVERING and not s.allows_entries
    with pytest.raises(IllegalSafetyTransition):
        s.recovered(T0, reconciled=False)


def test_halt_latches_raises_epoch_and_needs_operator() -> None:
    s = running()
    s.halt(T0, "drawdown")
    s.halt(T0, "again")                                              # idempotent
    assert s.epoch == 2 and s.state is SafetyState.HALTING
    s.halted_flat_confirmed(T0)
    with pytest.raises(IllegalSafetyTransition):
        s.recovered(T0, reconciled=True)
    s.operator_resume(T0, "Note")
    assert s.state is SafetyState.RECOVERING
    s.recovered(T0, reconciled=True)
    assert s.allows_entries


def test_reducing_cools_down_before_running() -> None:
    s = running()
    s.reduce(T0, "loss streak", timedelta(hours=4))
    assert not s.allows_entries
    assert s.maybe_end_reducing(T0 + timedelta(hours=3)) is False
    assert s.maybe_end_reducing(T0 + timedelta(hours=4)) is True and s.allows_entries


# --- pre-trade limits ------------------------------------------------------

MKT = MarketView(D("65000"), T0)
IDLE = Activity(active_orders=0, orders_today=0, cancels_today=0, last_submit_at=None)


def test_clean_intent_passes() -> None:
    assert check(intent(qty="0.03"), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=running()) == []


def test_limits_refuse_what_they_should() -> None:
    s = running()
    big = check(intent(qty="0.069"), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert big == ["ORDER_NOTIONAL_ABOVE_MAX"]                       # 0.069 * 65000 = 4485 > 2000
    stale = check(intent(qty="0.03"), now=T0 + timedelta(seconds=6), rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert stale == ["PRICE_STALE"]
    band = check(intent(qty="0.03", price="66000"), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert band == ["PRICE_OUTSIDE_BAND"]
    busy = Activity(active_orders=2, orders_today=20, cancels_today=50, last_submit_at=T0)
    assert set(check(intent(qty="0.03"), now=T0, rules=RULES, market=MKT, activity=busy, safety=s)) == {
        "RATE_LIMIT", "TOO_MANY_ACTIVE_ORDERS", "DAILY_ORDER_LIMIT"}
    assert check_cancel(busy) == ["DAILY_CANCEL_LIMIT"]


def test_entries_blocked_unless_running_but_exits_allowed() -> None:
    s = running()
    s.halt(T0, "kill")
    buy = check(intent(qty="0.03", epoch=2), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert buy == ["ENTRIES_BLOCKED_HALTING"]
    sell = check(intent(Side.SELL, qty="0.069", epoch=2), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert sell == []                                                 # size cap never blocks getting out


def test_intent_from_old_epoch_is_refused() -> None:
    s = running()
    s.halt(T0, "kill")
    out = check(intent(Side.SELL, qty="0.01", epoch=1), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=s)
    assert out == ["STALE_SAFETY_EPOCH"]


def test_custom_limits() -> None:
    lim = PreTradeLimits(max_order_notional=D("10000"))
    assert check(intent(), now=T0, rules=RULES, market=MKT, activity=IDLE, safety=running(), limits=lim) == []


# --- reservations ----------------------------------------------------------

def test_reservation_held_while_unknown_and_released_only_when_confirmed() -> None:
    ex = FakeExchange(RULES, mark=D("20000"), partial_qty=D("0.04"))
    book = ReservationBook(budget=D("3000"))
    o = Order(intent(qty="0.069"))
    book.reserve(o.client_order_id, o.intent.qty, D("20000"))
    book.reserve(o.client_order_id, o.intent.qty, D("20000"))      # same intent: no double reserve
    assert book.reserved() == D("1380")
    with pytest.raises(InsufficientBudget):
        book.reserve("other", D("0.1"), D("20000"))
    ex.plan(Behaviour.TIMEOUT_ACCEPTED, then=Behaviour.PARTIAL)
    gateway.submit(o, ex)
    book.sync(o)
    assert book.reserved() == D("1380")                             # UNKNOWN keeps all of it
    gateway.reconcile(o, ex)
    book.sync(o)
    assert book.reserved() == D("580")                              # 0.029 left open
    ex.cancel("BTCUSDT", o.client_order_id)
    gateway.reconcile(o, ex)
    book.sync(o)
    assert book.reserved() == 0
