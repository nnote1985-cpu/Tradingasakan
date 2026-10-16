"""Position sizing for linear base-quantity contracts — BUILDPLAN v4.2 §9.2–9.4.

Pure functions on Decimal. They do not reserve risk or read account state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def size_linear_position(*, budget, entry, stop, entry_fee, exit_fee,
                         stop_slip, funding_reserve, qty_step, qty_cap):
    """§9.4 verbatim: risk-budget quantity, floored to the exchange step."""
    values = [budget, entry, stop, entry_fee, exit_fee,
              stop_slip, funding_reserve, qty_step, qty_cap]
    b, e, s, ef, xf, slip, fund, step, cap = map(D, values)
    if any(not x.is_finite() for x in (b, e, s, ef, xf, slip, fund, step, cap)):
        raise ValueError("non-finite input")
    if min(b, e, s, step, cap) <= 0 or min(ef, xf, slip, fund) < 0 or e == s:
        raise ValueError("invalid sizing input")
    per_unit = abs(e - s) + e * ef + s * xf + slip + fund
    raw_qty = min(b / per_unit, cap)
    qty = (raw_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
    return {"qty": qty, "estimated_risk": qty * per_unit, "per_unit": per_unit}


def round_to_tick(price, tick, *, up: bool) -> Decimal:
    p, t = D(price), D(tick)
    mode = ROUND_CEILING if up else ROUND_FLOOR
    return (p / t).to_integral_value(rounding=mode) * t


def round_stop(stop, tick, side: str) -> Decimal:
    """LONG stop rounds up toward price, SHORT stop rounds down toward price (§9.2)."""
    return round_to_tick(stop, tick, up=(side == "LONG"))


def round_take_profit(tp, tick, side: str) -> Decimal:
    """TP rounds toward entry to avoid an optimistic target (§9.2)."""
    return round_to_tick(tp, tick, up=(side == "SHORT"))


@dataclass(frozen=True)
class RiskProfile:
    """DEFAULT research risk profile (§9.1)."""
    risk_per_trade_fraction: Decimal = Decimal("0.005")
    absolute_trade_risk_cap_usdt: Decimal = Decimal("50")
    daily_loss_trigger_fraction: Decimal = Decimal("0.02")
    portfolio_drawdown_halt_fraction: Decimal | None = Decimal("0.08")   # None: drawdown never halts
    max_gross_notional_to_equity: Decimal = Decimal("3")
    requested_exchange_leverage: Decimal = Decimal("3")
    max_margin_utilization_fraction: Decimal = Decimal("0.80")
    daily_loss_action: str = "HALT"                      # "HALT", or "ALERT": record and alert, keep trading
    drawdown_alert_fraction: Decimal | None = None       # alert (no halt) once per drawdown episode

    def __post_init__(self):
        if self.daily_loss_action not in ("HALT", "ALERT"):
            raise ValueError("daily_loss_action must be HALT or ALERT")


@dataclass(frozen=True)
class InstrumentRules:
    """EXAMPLE/DISCOVERY values — replace with verified exchange filters (§4.1)."""
    tick: Decimal = Decimal("0.1")
    qty_step: Decimal = Decimal("0.001")
    min_notional: Decimal = Decimal("100")
    max_qty: Decimal = Decimal("1000")


@dataclass(frozen=True)
class SizingAllowances:
    """Sizing allowances (not ledger costs). Fractions of price; DEFAULT assumptions."""
    entry_fee_rate: Decimal = Decimal("0.0005")
    exit_fee_rate: Decimal = Decimal("0.0005")
    stop_slip_fraction: Decimal = Decimal("0.001")
    funding_reserve_fraction: Decimal = Decimal("0.0003")


def plan_entry(*, side: str, entry_estimate, atr, stop_atr_mult, equity,
               remaining_day_budget, profile: RiskProfile, rules: InstrumentRules,
               allow: SizingAllowances) -> dict:
    """Stop + quantity for one proposal. Returns ``{"reject": code}`` on rejection."""
    e = D(entry_estimate)
    distance = D(atr) * D(stop_atr_mult)
    raw_stop = e - distance if side == "LONG" else e + distance
    stop = round_stop(raw_stop, rules.tick, side)
    if (side == "LONG" and stop >= e) or (side == "SHORT" and stop <= e) or stop <= 0:
        return {"reject": "REJECT_STOP_WRONG_SIDE"}

    eq = D(equity)
    budget = min(eq * profile.risk_per_trade_fraction, D(remaining_day_budget),
                 profile.absolute_trade_risk_cap_usdt)
    if budget <= 0:
        return {"reject": "REJECT_NO_RISK_BUDGET"}

    max_notional = min(eq * profile.max_gross_notional_to_equity,
                       eq * profile.max_margin_utilization_fraction * profile.requested_exchange_leverage)
    qty_cap = min(max_notional / e, rules.max_qty)
    sized = size_linear_position(
        budget=budget, entry=e, stop=stop,
        entry_fee=allow.entry_fee_rate, exit_fee=allow.exit_fee_rate,
        stop_slip=e * allow.stop_slip_fraction,
        funding_reserve=e * allow.funding_reserve_fraction,
        qty_step=rules.qty_step, qty_cap=qty_cap,
    )
    qty = sized["qty"]
    if qty <= 0 or qty * e < rules.min_notional:
        return {"reject": "REJECT_MIN_NOTIONAL"}
    return {"stop": stop, "qty": qty, "estimated_risk": sized["estimated_risk"], "budget": budget}
