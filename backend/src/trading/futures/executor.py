"""Carry out manual order requests on the Futures Demo (futures-desk-plan.md §7).

Runs inside the futures worker right after a sync, so every check uses exchange data a
moment old. A request is marked SENT before the first exchange call and is never sent
again: a worker that restarts with a SENT row marks it FAILED and lets the no-stop alert
and the page show what is on the exchange.

OPEN: margin type isolated, leverage, MARKET entry, then a closePosition STOP_MARKET on the
mark price. If the stop cannot be placed the position is closed at market at once.
CLOSE: cancel the symbol's open orders, then a reduce-only MARKET for the whole position.
MOVE_STOP: place the new stop first, then cancel the old ones, so the position is never bare.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from trading.futures.orders import Ticket, check_move_stop, plan_open, text
from trading.futures.rest import FuturesDemoRest, FuturesError
from trading.futures.risk import caps
from trading.futures.sync import RiskState, Snapshot
from trading.runtime.alerts import AlertQueue, Severity

MAX_AGE = timedelta(seconds=60)   # a ticket older than this was priced on a market that has moved: refuse it


class RequestStore(Protocol):
    def open_requests(self) -> list[dict[str, Any]]: ...
    def mark_request(self, rid: int, status: str, outcome: str | None = None, detail: dict[str, Any] | None = None) -> bool: ...


CLOSE_POSITION_EXISTS = -4130     # a closePosition stop/TP already exists in that direction


def _opposite(side: str) -> str:
    return "SELL" if side == "LONG" else "BUY"


class Executor:
    def __init__(self, rest: FuturesDemoRest, store: RequestStore, alerts: AlertQueue, *, log: Callable[[str], None] = print) -> None:
        self.rest, self.store, self.alerts, self.log = rest, store, alerts, log
        self._started = False

    def run(self, snap: Snapshot, risk: RiskState, now: datetime) -> int:
        """Handle every open request once. Returns how many were handled."""
        n = 0
        for req in self.store.open_requests():
            if req["status"] == "SENT":
                if not self._started:   # left by an earlier run that stopped mid-send
                    self.store.mark_request(req["id"], "FAILED", "worker หยุดระหว่างส่งคำสั่ง ให้ดู position และคำสั่งค้างบนหน้านี้")
                    self.alerts.push(Severity.CRITICAL, f"fut-req-{req['id']}", f"คำสั่ง #{req['id']} ค้างสถานะกำลังส่งหลัง worker เริ่มใหม่ "
                                     "ให้เช็ก position และ Stop-loss บนหน้า Futures")
                continue
            if now - req["requested_at"] > MAX_AGE:
                self.store.mark_request(req["id"], "EXPIRED", "คำสั่งรอนานเกิน 60 วินาที ราคาอาจเปลี่ยนแล้ว ส่งใหม่จากหน้าเว็บ")
                continue
            n += 1
            try:
                if req["kind"] == "OPEN":
                    self._open(req, snap, risk)
                elif req["kind"] == "CLOSE":
                    self._close(req, snap)
                elif req["kind"] == "MOVE_STOP":
                    self._move_stop(req, snap)
            except Exception as e:  # noqa: BLE001 - the row records the failure; the next request still runs
                msg = f"{type(e).__name__}: {e}"[:400]
                self.log(f"request {req['id']} failed: {msg}")
                self.store.mark_request(req["id"], "FAILED", msg)
                self.alerts.push(Severity.CRITICAL, f"fut-req-{req['id']}", f"คำสั่ง #{req['id']} ({req['kind']} {req['symbol']}) ไม่สำเร็จ: {msg}")
        self._started = True
        return n

    # --- kinds ---------------------------------------------------------------

    def _open(self, req: dict[str, Any], snap: Snapshot, risk: RiskState) -> None:
        rid, sym, side = req["id"], req["symbol"], req["side"]
        ticket = Ticket(sym, side, Decimal(req["stop_price"]), int(req["leverage"]),
                        Decimal(req["take_profit"]) if req["take_profit"] is not None else None)
        plan = plan_open(ticket, equity=snap.margin_balance, available=snap.available_balance, positions=snap.positions,
                         orders=snap.open_orders, markets=snap.markets, caps=caps(snap.margin_balance, risk))
        if not plan.ok:
            self.store.mark_request(rid, "REFUSED", " · ".join(plan.refusals), {"plan": plan.view()})
            return
        qty = min(plan.qty, Decimal(req["qty"]))       # never more than the size the page showed
        if not self.store.mark_request(rid, "SENT", None, {"plan": plan.view(), "qty_sent": text(qty)}):
            return
        self.rest.set_margin_isolated(sym)
        self.rest.set_leverage(sym, plan.leverage)
        entry = self.rest.market_order(sym, "BUY" if side == "LONG" else "SELL", text(qty), f"fd{rid}e")
        filled = Decimal(str(entry.get("executedQty") or 0))
        if filled <= 0:
            self.store.mark_request(rid, "FAILED", f"คำสั่งเข้าไม่ถูกจับคู่ (สถานะ {entry.get('status')})", {"entry": entry})
            return
        try:
            sl = self.rest.conditional_close(sym, _opposite(side), "STOP_MARKET", text(plan.stop_price), f"fd{rid}s")
        except Exception as e:  # noqa: BLE001 - no stop means no position: close it now
            self.log(f"request {rid}: stop failed ({e}); closing at market")
            out = self.rest.market_order(sym, _opposite(side), text(filled), f"fd{rid}x", reduce_only=True)
            self.store.mark_request(rid, "FAILED", f"ตั้ง Stop-loss ไม่สำเร็จ จึงปิด position ทันที ({type(e).__name__}: {e})"[:400],
                                    {"entry": entry, "emergency_close": out})
            self.alerts.push(Severity.CRITICAL, f"fut-req-{rid}", f"ตั้ง SL ของ {sym} ไม่สำเร็จ ปิด position ทันทีแล้ว ให้เช็กหน้า Futures")
            return
        detail: dict[str, Any] = {"entry": entry, "stop": sl}
        note = None
        if plan.take_profit is not None:
            try:
                try:
                    detail["take_profit"] = self.rest.conditional_close(sym, _opposite(side), "TAKE_PROFIT_MARKET",
                                                                        text(plan.take_profit), f"fd{rid}t")
                except FuturesError as e:
                    if e.code != CLOSE_POSITION_EXISTS:
                        raise
                    detail["take_profit"] = self.rest.conditional_reduce(sym, _opposite(side), "TAKE_PROFIT_MARKET",
                                                                         text(plan.take_profit), text(filled), f"fd{rid}t")
            except Exception as e:  # noqa: BLE001 - the stop is in place; a missing TP is only noted
                note = f"ตั้ง Take-profit ไม่สำเร็จ ({type(e).__name__}) มี Stop-loss แล้ว"
        avg = self._avg_price(sym, entry)
        self.store.mark_request(rid, "DONE", note or f"เปิด {side} {text(filled)} {sym} ที่ {avg} SL {text(plan.stop_price)}", detail)
        self.alerts.push(Severity.INFO, f"fut-req-{rid}", f"เปิด {side} {text(filled)} {sym} ที่ {avg} SL {text(plan.stop_price)}"
                         f"{' TP ' + text(plan.take_profit) if plan.take_profit else ''} เสี่ยง {text(plan.risk_usdt.quantize(Decimal('0.01')))} USDT")

    def _close(self, req: dict[str, Any], snap: Snapshot) -> None:
        rid, sym = req["id"], req["symbol"]
        pos = next((p for p in snap.positions if p["symbol"] == sym), None)
        has_orders = any(o["symbol"] == sym for o in snap.open_orders)
        if pos is None and not has_orders:
            self.store.mark_request(rid, "REFUSED", f"ไม่มี position หรือคำสั่งค้างของ {sym}")
            return
        if not self.store.mark_request(rid, "SENT"):
            return
        detail: dict[str, Any] = {}
        if pos is not None:
            detail["close"] = self.rest.market_order(sym, _opposite(pos["side"]), pos["qty"], f"fd{rid}c", reduce_only=True)
        self.rest.cancel_all(sym)    # after the close, so the stop guards the position until it is gone
        self.store.mark_request(rid, "DONE", f"ปิด {sym} แล้ว" if pos else f"ล้างคำสั่งค้าง {sym} แล้ว", detail)

    def _move_stop(self, req: dict[str, Any], snap: Snapshot) -> None:
        rid, sym = req["id"], req["symbol"]
        pos = next((p for p in snap.positions if p["symbol"] == sym), None)
        if pos is None:
            self.store.mark_request(rid, "REFUSED", f"ไม่มี position {sym}")
            return
        m = snap.markets[sym]
        stop, refusals = check_move_stop(pos, Decimal(req["stop_price"]), snap.margin_balance, Decimal(m["mark"]), Decimal(m["tick_size"]))
        if refusals:
            self.store.mark_request(rid, "REFUSED", " · ".join(refusals))
            return
        if not self.store.mark_request(rid, "SENT"):
            return
        old = [o for o in snap.open_orders if o["symbol"] == sym and o["kind"] == "SL" and o["source"] == "algo"]
        side = _opposite(pos["side"])
        try:
            # A fixed-quantity reduce-only stop can sit next to the old closePosition stop (two closePosition stops
            # in one direction are refused with -4130), so the position is never without a stop.
            new = self.rest.conditional_reduce(sym, side, "STOP_MARKET", text(stop), pos["qty"], f"fd{rid}s")
        except Exception as e:  # noqa: BLE001 - fall back to swapping the stop, guarded below
            self.log(f"request {rid}: new stop next to the old one failed ({e}); swapping")
            self._swap_stop(rid, sym, side, pos, stop, old)
            return
        for o in old:
            self.rest.cancel_algo(o["id"])
        self.store.mark_request(rid, "DONE", f"ย้าย Stop-loss {sym} ไปที่ {text(stop)}",
                                {"stop": new, "cancelled": [o["id"] for o in old]})

    def _swap_stop(self, rid: int, sym: str, side: str, pos: dict[str, Any], stop: Decimal, old: list[dict[str, Any]]) -> None:
        """Cancel the old stop, then place the new one. If that fails, put the old stop back; if even that fails,
        close the position at market, because a position must never be left without a stop."""
        for o in old:
            self.rest.cancel_algo(o["id"])
        try:
            new = self.rest.conditional_close(sym, side, "STOP_MARKET", text(stop), f"fd{rid}w")
            self.store.mark_request(rid, "DONE", f"ย้าย Stop-loss {sym} ไปที่ {text(stop)}",
                                    {"stop": new, "cancelled": [o["id"] for o in old], "swapped": True})
            return
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        try:
            if not old:
                raise RuntimeError("no old stop to restore")
            back = self.rest.conditional_close(sym, side, "STOP_MARKET", str(old[0]["trigger_price"]), f"fd{rid}r")
            self.store.mark_request(rid, "FAILED", f"ย้าย Stop-loss ไม่สำเร็จ คง Stop-loss เดิมไว้ ({err})"[:400], {"restored": back})
            self.alerts.push(Severity.WARNING, f"fut-req-{rid}", f"ย้าย SL ของ {sym} ไม่สำเร็จ คง SL เดิม {old[0]['trigger_price']}")
        except Exception as e2:  # noqa: BLE001 - no stop at all: close the position now
            out = self.rest.market_order(sym, side, pos["qty"], f"fd{rid}x", reduce_only=True)
            self.rest.cancel_all(sym)
            self.store.mark_request(rid, "FAILED", f"ย้าย Stop-loss ไม่สำเร็จและตั้งคืนไม่ได้ จึงปิด position ทันที ({err}; {e2})"[:400],
                                    {"emergency_close": out})
            self.alerts.push(Severity.CRITICAL, f"fut-req-{rid}", f"ย้าย SL ของ {sym} ไม่สำเร็จ ปิด position ทันทีแล้ว ให้เช็กหน้า Futures")

    def _avg_price(self, sym: str, entry: dict[str, Any]) -> str:
        """The fill price for messages. Demo's MARKET reply carries no avgPrice, so ask for the order once."""
        avg = entry.get("avgPrice")
        if not avg or Decimal(str(avg)) <= 0:
            try:
                avg = self.rest.order(sym, entry.get("orderId")).get("avgPrice")
            except Exception:  # noqa: BLE001 - only the message loses the price
                avg = None
        return text(Decimal(str(avg))) if avg and Decimal(str(avg)) > 0 else "ราคาตลาด"
