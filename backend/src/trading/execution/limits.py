"""Pre-trade limits, checked before every submit (vnpy RiskManager style).

DEFAULT values are the testnet ones from docs/testnet-development-plan.md §10.2
(quote freshness 5 s per §4.2; the rest as declared in docs/phase-4-execution-plan.md).
Orders that only reduce exposure (spot sells) skip the size and price-band
checks and the normal active-order and daily-order caps, so a limit can never
block getting out; they still respect the rate limit, the exchange rules and a
separate emergency ceiling that only stops a runaway loop. Cancels of buy
orders likewise skip the normal daily cancel cap up to that ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading.execution.intent import OrderIntent
from trading.execution.rules import TradingRules
from trading.execution.safety import AccountSafety
from trading.execution.types import OrderType


@dataclass(frozen=True)
class PreTradeLimits:
    max_order_notional: Decimal = Decimal("2000")
    min_seconds_between_orders: Decimal = Decimal("1")
    max_active_orders: int = 2
    max_orders_per_day: int = 20
    max_cancels_per_day: int = 50
    max_price_age: timedelta = timedelta(seconds=5)          # testnet-development-plan.md §4.2
    max_price_deviation: Decimal = Decimal("0.01")
    # Exits and cancels of buys are never held by the caps above; this only stops a runaway loop of them.
    emergency_per_day: int = 200


@dataclass(frozen=True)
class MarketView:
    """Latest mark for the symbol and when it was observed."""

    mark: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class Activity:
    """Counts the gateway keeps for the account (UTC day)."""

    active_orders: int
    orders_today: int
    cancels_today: int
    last_submit_at: datetime | None


def check(intent: OrderIntent, *, now: datetime, rules: TradingRules, market: MarketView, activity: Activity,
          safety: AccountSafety, limits: PreTradeLimits | None = None) -> list[str]:
    """Reasons to refuse ``intent``; an empty list means it may be submitted."""
    lim = limits or PreTradeLimits()
    out: list[str] = []
    if intent.symbol != rules.symbol:
        out.append("SYMBOL_MISMATCH")
    if intent.safety_epoch != safety.epoch:
        out.append("STALE_SAFETY_EPOCH")
    if intent.increases_exposure and not safety.allows_entries:
        out.append(f"ENTRIES_BLOCKED_{safety.state}")
    if now - market.observed_at > lim.max_price_age or market.observed_at > now:
        out.append("PRICE_STALE")
    if activity.last_submit_at is not None and (now - activity.last_submit_at).total_seconds() < lim.min_seconds_between_orders:
        out.append("RATE_LIMIT")
    if intent.increases_exposure:
        if activity.active_orders >= lim.max_active_orders:
            out.append("TOO_MANY_ACTIVE_ORDERS")
        if activity.orders_today >= lim.max_orders_per_day:
            out.append("DAILY_ORDER_LIMIT")
    elif activity.orders_today >= lim.emergency_per_day:
        out.append("EMERGENCY_ORDER_CEILING")
    price = intent.limit_price if intent.order_type is OrderType.LIMIT and intent.limit_price is not None else market.mark
    out.extend(rules.violations(intent.qty, price))
    if intent.increases_exposure:
        if intent.qty * price > lim.max_order_notional:
            out.append("ORDER_NOTIONAL_ABOVE_MAX")
        if market.mark > 0 and abs(price - market.mark) / market.mark > lim.max_price_deviation:
            out.append("PRICE_OUTSIDE_BAND")
    return out


def check_cancel(activity: Activity, limits: PreTradeLimits | None = None, *, reduces_risk: bool = False) -> list[str]:
    """Reasons to hold a cancel. Canceling a buy (``reduces_risk``) only stops at the emergency ceiling."""
    lim = limits or PreTradeLimits()
    if reduces_risk:
        return ["EMERGENCY_CANCEL_CEILING"] if activity.cancels_today >= lim.emergency_per_day else []
    return ["DAILY_CANCEL_LIMIT"] if activity.cancels_today >= lim.max_cancels_per_day else []
