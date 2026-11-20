"""Desk risk checks against the standing rules (futures-desk-plan.md §2). Pure functions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading.futures import rules
from trading.futures.sync import RiskState


def _pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.01'))}%"


def caps(equity: Decimal, risk: RiskState) -> dict[str, Any]:
    """Day loss (vs the UTC day open) and total drawdown (vs the high-water mark), each as a share of the cap used."""
    day_loss = max(Decimal(0), (risk.day_open - equity) / risk.day_open) if risk.day_open > 0 else Decimal(0)
    drawdown = max(Decimal(0), (risk.hwm - equity) / risk.hwm) if risk.hwm > 0 else Decimal(0)
    day_used, total_used = day_loss / rules.DAY_LOSS_CAP, drawdown / rules.TOTAL_LOSS_CAP
    reasons = []
    if day_loss >= rules.DAY_LOSS_CAP:
        reasons.append(f"วันนี้ขาดทุน {_pct(day_loss)} ถึงเพดาน {_pct(rules.DAY_LOSS_CAP)}")
    if drawdown >= rules.TOTAL_LOSS_CAP:
        reasons.append(f"ลดจากจุดสูงสุด {_pct(drawdown)} ถึงเพดาน {_pct(rules.TOTAL_LOSS_CAP)}")

    def level(used: Decimal) -> str:
        return "BLOCKED" if used >= 1 else "NEAR" if used >= rules.NEAR else "OK"

    return {
        "day": {"loss": day_loss, "cap": rules.DAY_LOSS_CAP, "used": min(day_used, Decimal(1)), "level": level(day_used),
                "day_open": risk.day_open, "day": risk.day.isoformat(),
                "room": max(Decimal(0), equity - risk.day_open * (1 - rules.DAY_LOSS_CAP))},
        "total": {"loss": drawdown, "cap": rules.TOTAL_LOSS_CAP, "used": min(total_used, Decimal(1)), "level": level(total_used),
                  "hwm": risk.hwm, "hwm_at": risk.hwm_at.isoformat(),
                  "room": max(Decimal(0), equity - risk.hwm * (1 - rules.TOTAL_LOSS_CAP))},
        "blocked": bool(reasons),
        "reasons": reasons,
    }


def position_risk(position: dict[str, Any], orders: list[dict[str, Any]], equity: Decimal) -> dict[str, Any]:
    """What a stop-out would cost now, from the position's stop on the exchange, and how far liquidation is.

    The loss is measured from the mark price, so it shrinks as the trade moves in favour; it is negative
    (a locked-in gain) once the stop is past the entry.
    """
    sym, long = position["symbol"], position["side"] == "LONG"
    qty, mark = Decimal(position["qty"]), Decimal(position["mark_price"])
    entry = Decimal(position["entry_price"])
    stops = [Decimal(o["trigger_price"]) for o in orders
             if o["symbol"] == sym and o["kind"] == "SL" and o.get("trigger_price") and o["side"] == ("SELL" if long else "BUY")]
    tps = [Decimal(o["trigger_price"]) for o in orders
           if o["symbol"] == sym and o["kind"] == "TP" and o.get("trigger_price") and o["side"] == ("SELL" if long else "BUY")]
    # The tightest stop is the one that fires first.
    stop = (max(stops) if long else min(stops)) if stops else None
    tp = (min(tps) if long else max(tps)) if tps else None
    out: dict[str, Any] = {"stop_price": stop, "take_profit": tp, "has_stop": stop is not None}
    if stop is not None:
        loss_now = (mark - stop) * qty if long else (stop - mark) * qty
        loss_entry = (entry - stop) * qty if long else (stop - entry) * qty
        out |= {"loss_at_stop": loss_now, "loss_at_stop_pct": loss_now / equity if equity > 0 else None,
                "initial_risk": loss_entry, "initial_risk_pct": loss_entry / equity if equity > 0 else None,
                "over_rule": equity > 0 and loss_entry / equity > rules.RISK_PER_TRADE * Decimal("1.1")}
    liq = position.get("liquidation_price")
    if liq:
        lq = Decimal(liq)
        out["liq_distance_pct"] = abs(mark - lq) / mark if mark > 0 else None
        out["stop_before_liq"] = stop is None or (stop > lq if long else stop < lq)
    return out
