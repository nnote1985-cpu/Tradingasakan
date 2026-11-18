"""Manual order tickets: checks and sizing (futures-desk-plan.md §7). Pure functions.

The API runs :func:`plan_open` to preview and to accept a ticket; the worker runs it again
on fresh exchange data just before sending, and never sends more than the size that was
previewed. Every refusal is a sentence in Thai that the page shows as is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Any

from trading.futures import rules

MAX_LEVERAGE = 10
MIN_STOP_DISTANCE = Decimal("0.001")    # 0.1%: closer and the stop may fire on the spread, and the size balloons
MAX_STOP_DISTANCE = Decimal("0.15")
MMR_ESTIMATE = Decimal("0.005")         # maintenance margin rate used for the liquidation estimate (BTC tier 1 is 0.4%)
LIQ_BUFFER = Decimal("0.002")           # the stop must sit at least 0.2% inside the estimated liquidation price
MARGIN_SHARE = Decimal("0.9")           # keep 10% of the available balance free
RISK_TOLERANCE = Decimal("1.1")         # moving a stop may not push risk at entry above 1.1% of equity


def step_floor(v: Decimal, step: Decimal) -> Decimal:
    return (v / step).to_integral_value(ROUND_DOWN) * step if step > 0 else v


def tick_round(v: Decimal, tick: Decimal, *, down: bool) -> Decimal:
    if tick <= 0:
        return v
    return (v / tick).to_integral_value(ROUND_FLOOR if down else ROUND_CEILING) * tick


def text(v: Decimal) -> str:
    t = format(v.normalize(), "f")
    return "0" if t == "-0" else t


@dataclass(frozen=True)
class Ticket:
    symbol: str
    side: str                        # LONG / SHORT
    stop_price: Decimal
    leverage: int
    take_profit: Decimal | None = None


@dataclass
class Plan:
    ok: bool
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    qty: Decimal = Decimal(0)
    ref_price: Decimal = Decimal(0)
    stop_price: Decimal = Decimal(0)
    take_profit: Decimal | None = None
    leverage: int = 1
    risk_usdt: Decimal = Decimal(0)
    risk_pct: Decimal = Decimal(0)
    risk_budget: Decimal = Decimal(0)
    notional: Decimal = Decimal(0)
    margin: Decimal = Decimal(0)
    liq_estimate: Decimal | None = None

    def view(self) -> dict[str, Any]:
        def t(v: Any) -> Any:
            return text(v) if isinstance(v, Decimal) else v
        return {k: t(v) for k, v in self.__dict__.items()}


def liq_estimate(side: str, price: Decimal, leverage: int) -> Decimal:
    """Isolated-margin liquidation estimate for a fresh position (no fees, flat maintenance rate)."""
    inv = Decimal(1) / leverage
    return price * (1 - inv + MMR_ESTIMATE) if side == "LONG" else price * (1 + inv - MMR_ESTIMATE)


def plan_open(t: Ticket, *, equity: Decimal, available: Decimal, positions: list[dict[str, Any]],
              orders: list[dict[str, Any]], markets: dict[str, dict[str, Any]], caps: dict[str, Any] | None) -> Plan:
    p = Plan(ok=False, leverage=t.leverage)
    r = p.refusals
    if t.symbol not in rules.SYMBOLS:
        r.append(f"หน้านี้เทรดได้เฉพาะ {', '.join(rules.SYMBOLS)}")
        return p
    if t.side not in ("LONG", "SHORT"):
        r.append("เลือกฝั่ง Long หรือ Short")
        return p
    m = markets.get(t.symbol)
    mark = Decimal(str(m.get("mark") or 0)) if m else Decimal(0)
    if not m or mark <= 0:
        r.append(f"ยังไม่มีราคา {t.symbol} จาก worker")
        return p
    if not 1 <= t.leverage <= MAX_LEVERAGE:
        r.append(f"Leverage ต้องอยู่ระหว่าง 1 ถึง {MAX_LEVERAGE}")
    if caps is None:
        r.append("ยังไม่มีข้อมูลเพดานขาดทุน รอ worker อ่านบัญชีรอบแรก")
    elif caps["blocked"]:
        r.append("ถึงเพดานขาดทุนแล้ว ห้ามเปิดไม้ใหม่: " + " · ".join(caps["reasons"]))
    if any(x["symbol"] == t.symbol for x in positions):
        r.append(f"มี position {t.symbol} เปิดอยู่แล้ว หน้านี้เปิดได้ทีละไม้ต่อคู่ ปิดไม้เดิมก่อน")
    protected = {o["symbol"] for o in orders if o["kind"] == "SL"}
    bare = [x["symbol"] for x in positions if x["symbol"] not in protected]
    if bare:
        r.append(f"มี position ที่ไม่มี Stop-loss ({', '.join(bare)}) ตั้ง SL หรือปิดก่อนเปิดไม้ใหม่")
    if any(o["symbol"] == t.symbol for o in orders) and not any(x["symbol"] == t.symbol for x in positions):
        r.append(f"มีคำสั่งค้างของ {t.symbol} จากไม้ก่อน กด 'ปิด / ล้างคำสั่ง' ก่อน")

    long = t.side == "LONG"
    tick, step = Decimal(str(m["tick_size"])), Decimal(str(m["step_size"]))
    stop = tick_round(t.stop_price, tick, down=long)          # round away from the price: never a tighter stop than asked
    dist = (mark - stop) / mark if long else (stop - mark) / mark
    if stop <= 0 or dist <= 0:
        r.append("Stop-loss ต้องอยู่ต่ำกว่าราคาตอนนี้สำหรับ Long และสูงกว่าสำหรับ Short")
    elif dist < MIN_STOP_DISTANCE:
        r.append(f"Stop-loss ใกล้ราคาเกินไป (ห่าง {(dist * 100).quantize(Decimal('0.01'))}% ต้องห่างอย่างน้อย 0.1%)")
    elif dist > MAX_STOP_DISTANCE:
        r.append(f"Stop-loss ห่างเกิน 15% ({(dist * 100).quantize(Decimal('0.01'))}%)")
    tp = None
    if t.take_profit is not None:
        tp = tick_round(t.take_profit, tick, down=not long)
        if (long and tp <= mark) or (not long and tp >= mark):
            r.append("Take-profit ต้องอยู่สูงกว่าราคาตอนนี้สำหรับ Long และต่ำกว่าสำหรับ Short")
    p.ref_price, p.stop_price, p.take_profit = mark, stop, tp
    if r:
        return p

    budget = equity * rules.RISK_PER_TRADE
    if caps is not None:
        room = min(Decimal(str(caps["day"]["room"])), Decimal(str(caps["total"]["room"])))
        if room < budget:
            p.notes.append("ใกล้เพดานขาดทุน ไม้นี้จึงเล็กกว่าปกติ ให้โดน SL แล้วยังไม่เกินเพดาน")
            budget = room
    p.risk_budget = budget
    per_unit = abs(mark - stop)
    qty = step_floor(budget / per_unit, step)
    p.qty, p.notional = qty, qty * mark
    p.risk_usdt = qty * per_unit
    p.risk_pct = p.risk_usdt / equity if equity > 0 else Decimal(0)
    p.margin = p.notional / t.leverage
    p.liq_estimate = liq_estimate(t.side, mark, t.leverage)
    if qty <= 0 or qty < Decimal(str(m["min_qty"])):
        r.append(f"ขนาดไม้ที่คำนวณได้ ({text(qty)}) เล็กกว่าขั้นต่ำ {m['min_qty']} ขยับ Stop-loss ให้ใกล้ขึ้น")
    elif p.notional < Decimal(str(m["min_notional"])):
        r.append(f"มูลค่าไม้ {text(p.notional.quantize(Decimal('0.01')))} USDT ต่ำกว่าขั้นต่ำ {m['min_notional']} USDT ขยับ Stop-loss ให้ใกล้ขึ้น")
    if p.margin > available * MARGIN_SHARE:
        r.append(f"Margin ที่ต้องใช้ {text(p.margin.quantize(Decimal('0.01')))} USDT เกินยอดที่ใช้ได้ เพิ่ม Leverage หรือขยับ Stop-loss")
    liq = p.liq_estimate
    if (long and stop <= liq * (1 + LIQ_BUFFER)) or (not long and stop >= liq * (1 - LIQ_BUFFER)):
        r.append(f"ราคา Liquidation โดยประมาณ ({text(liq.quantize(tick))}) มาก่อน Stop-loss ลด Leverage")
    p.ok = not r
    return p


def check_move_stop(position: dict[str, Any], new_stop: Decimal, equity: Decimal, mark: Decimal, tick: Decimal) -> tuple[Decimal, list[str]]:
    """A new stop for an open position: on the right side of the mark, inside liquidation, risk at entry within the rule."""
    long = position["side"] == "LONG"
    stop = tick_round(new_stop, tick, down=long)
    refusals = []
    if (long and stop >= mark * (1 - MIN_STOP_DISTANCE)) or (not long and stop <= mark * (1 + MIN_STOP_DISTANCE)):
        refusals.append("Stop-loss ใหม่ต้องห่างจากราคาตอนนี้อย่างน้อย 0.1% และอยู่ฝั่งขาดทุนของราคา")
    entry, qty = Decimal(position["entry_price"]), Decimal(position["qty"])
    risk = (entry - stop) * qty if long else (stop - entry) * qty
    if equity > 0 and risk / equity > rules.RISK_PER_TRADE * RISK_TOLERANCE:
        refusals.append("Stop-loss ใหม่ทำให้ความเสี่ยงตอนเข้าเกิน 1% ของเงินในบัญชี ขยับได้แค่ไปทางลดความเสี่ยง")
    liq = position.get("liquidation_price")
    if liq and ((long and stop <= Decimal(liq)) or (not long and stop >= Decimal(liq))):
        refusals.append("Stop-loss ใหม่อยู่เลยราคา Liquidation")
    return stop, refusals
