"""Exchange trading rules for one symbol, and all price/quantity rounding.

Every price and quantity sent to an exchange goes through :class:`TradingRules`
so rounding happens in one place, in Decimal. Quantities round down to the step
(never send more than was planned). Limit prices round to the tick on the
passive side: a buy rounds down and a sell rounds up, so rounding never makes
an order more aggressive than planned.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from trading.execution.types import Side


def _to_step(x: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (x / step).to_integral_value(rounding=rounding) * step


@dataclass(frozen=True)
class TradingRules:
    """Binance-style PRICE_FILTER, LOT_SIZE and MIN_NOTIONAL for one symbol."""

    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        for name in ("tick_size", "step_size", "min_qty", "max_qty", "min_notional"):
            v = getattr(self, name)
            if not isinstance(v, Decimal) or not v.is_finite() or v < 0:
                raise ValueError(f"{name} must be a finite non-negative Decimal")
        if self.tick_size == 0 or self.step_size == 0:
            raise ValueError("tick_size and step_size must be positive")
        if self.max_qty < self.min_qty:
            raise ValueError("max_qty is below min_qty")

    def round_qty(self, qty: Decimal) -> Decimal:
        return _to_step(qty, self.step_size, ROUND_FLOOR)

    def round_price(self, price: Decimal, side: Side) -> Decimal:
        return _to_step(price, self.tick_size, ROUND_FLOOR if side is Side.BUY else ROUND_CEILING)

    def violations(self, qty: Decimal, price: Decimal) -> list[str]:
        """Why an order of ``qty`` at ``price`` would be refused; empty if it is valid.

        ``price`` is the limit price, or the reference mark for a market order.
        """
        out: list[str] = []
        if qty <= 0:
            out.append("QTY_NOT_POSITIVE")
            return out
        if qty % self.step_size != 0:
            out.append("QTY_NOT_ON_STEP")
        if qty < self.min_qty:
            out.append("QTY_BELOW_MIN")
        if qty > self.max_qty:
            out.append("QTY_ABOVE_MAX")
        if price <= 0:
            out.append("PRICE_NOT_POSITIVE")
        elif qty * price < self.min_notional:
            out.append("NOTIONAL_BELOW_MIN")
        return out
