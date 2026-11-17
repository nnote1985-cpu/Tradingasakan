"""Advance the forward pathA-r10a-hedge book, option "3a" (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade_r10a_hedge.py [--state PATH]

docs/r10a-hedge-book-plan.md. The decision is pathA-r10a's (scripts/paper_trade_r10a.py: ±2% band
latch on SMA100 of perp closes times path A's funding factor). The book holds spot w + 0.5(1 - w) and
a perp short of 0.5(1 - w), so when R10a is out it stays hedged instead of selling, and collects
funding. Spot fills and marks on BTCUSDT spot, perp on the USD-M perpetual; the short has a resting
stop at 3 x ATR14. A HALT closes both legs and waits for a resume record; this script never writes one.
State lives under /mnt/project-files/paper-trading/pathA-r10a-hedge/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading.alerts.live_data import (
    FUNDING_POLICY,
    daily_funding,
    daily_klines,
    hourly_klines,
    missing_days,
    spot_daily_klines,
    spot_hourly_klines,
)
from trading.alerts.risk_check import events, missing_inputs, signals
from trading.features.indicators import atr_wilder
from trading.shadow.hedge_book import RULES_VERSION, HedgeBook, targets
from trading.strategies.sma_band import BAND, band_state

SYMBOL = "BTCUSDT"
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json"
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ",
         "alert": "แจ้งเตือน", "halt": "หยุด (HALT)", "ignored_halted": "ไม่ทำตาม (หยุดอยู่)", "resume": "เริ่มใหม่"}

_spec = importlib.util.spec_from_file_location("paper_trade_v11", Path(__file__).with_name("paper_trade.py"))
assert _spec and _spec.loader
_v11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v11)
decision_inputs = _v11.decision_inputs


def target_th(w: float) -> str:
    s, p = targets(w)
    return f"spot {float(s):.0%} / short perp {float(-p):.0%}"


def write_journal(book: HedgeBook, path: Path) -> None:
    lines = [f"# สมุดบันทึกพอร์ตจำลอง {RULES_VERSION} (3a: R10a + hedge ด้วย short perp, เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; เกณฑ์ spot.v1 บนเงินรวมสองกระเป๋า: แจ้งเตือนเมื่อขาดทุน 5%/วัน หรือ drawdown 15%, หยุดที่ drawdown 25%; "
             "short มี stop ที่ราคาเข้า + 3 x ATR14", "",
             "| ราคาปิดวันที่ | เวลา | ประเภท | R10a | รายละเอียด |", "| --- | --- | --- | --- | --- |"]
    for e in book.journal:
        w = e.get("weight")
        detail = "; ".join(e.get("notes", [])) or e.get("alert") or e.get("reason") or ""
        if e["kind"] == "trade":
            detail = f"{e['leg']} {e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}" + (
                f" (stop {float(e['stop']):,.2f})" if e.get("reason") == "stop" else "")
        lines.append(f"| {e.get('decided_on', e.get('date', ''))} | {e.get('at', e.get('time', e.get('decided_at', '')))} "
                     f"| {KINDS.get(e['kind'], e['kind'])} | {'' if w is None else f'{w:.0%}'} | {detail} |")
    path.write_text("\n".join(lines) + "\n")


def render_th(p: dict, added: list[dict], problems: list[str]) -> str:
    lines = []
    if p["days"]:
        lines += [f"พอร์ตจำลอง {RULES_VERSION} (3a) {p['since']} ถึง {p['through']} ({p['days']} วัน)",
                  f"- โปรแกรม: {p['equity']:,.0f} USDT ({p['return']:+.1%}), ลงหนักสุด {p['max_dd']:.1%}, ซื้อขาย {p['trades']} ครั้ง",
                  f"- ซื้อ BTC ถือเฉยๆ: {p['buy_and_hold']:,.0f} USDT ({p['bh_return']:+.1%}), ลงหนักสุด {p['bh_max_dd']:.1%}",
                  f"- R10a {p['weight_held']:.0%}: spot {float(p['spot_weight']):.0%}, perp {float(p['perp_weight']):+.0%}, "
                  f"funding รับสุทธิ {float(p['funding_received']):,.2f} USDT"]
        if p.get("stop"):
            lines.append(f"- stop ของ short {float(p['stop']):,.2f}, ราคา liquidation {float(p['liquidation_price']):,.2f}")
    else:
        lines.append(f"พอร์ตจำลอง {RULES_VERSION} (3a) ยังไม่มีวันที่ปิดหลังซื้อขาย")
    if p.get("halted"):
        lines.append(f"- หยุดอยู่ (HALT {p.get('halt_reason')}) รอ Note สั่งเริ่มใหม่")
    for e in added:
        if e["kind"] == "decision":
            lines.append(f"- ตัดสินใจจากราคาปิด {e['decided_on']}: R10a {e['weight']:.0%} → {target_th(e['weight'])}")
        elif e["kind"] == "trade":
            lines.append(f"- {e['at']}: {e['leg']} {e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}"
                         + (" (stop)" if e.get("reason") == "stop" else ""))
        elif e["kind"] == "alert":
            lines.append(f"- แจ้งเตือน {e['alert']} ที่ {e['time']}")
        elif e["kind"] == "halt":
            lines.append(f"- HALT {e['reason']}: ปิด short และขาย BTC ทั้งหมดที่ {e['fill_at']}")
        elif e["kind"] in ("no_decision", "ignored_halted"):
            lines.append(f"- {KINDS[e['kind']]} ราคาปิด {e['decided_on']}: {', '.join(e['notes'])}")
    lines += [f"- ปัญหา: {x}" for x in problems]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args()
    started = datetime.now(UTC)
    end = started.date() - timedelta(days=1)
    state_path = Path(args.state)
    book = HedgeBook(json.loads(state_path.read_text())) if state_path.exists() else None

    # Every input is fetched before any time is stamped.
    start = end - timedelta(days=400)
    daily = daily_klines(SYMBOL, start, end)                       # perp: signals, ATR, marks
    first = (datetime.fromisoformat(book.last_day) + timedelta(days=1)).date() if book and book.last_day else end
    funding, est = daily_funding(SYMBOL, min(first, end - timedelta(days=40)) - timedelta(days=1), end)
    spot_daily = spot_daily_klines(SYMBOL, first - timedelta(days=1), end)
    have_book = book is not None and first <= end
    spot_hourly = spot_hourly_klines(SYMBOL, first, end) if have_book else spot_daily.iloc[:0]
    perp_hourly = hourly_klines(SYMBOL, first, end) if have_book else daily.iloc[:0]
    data_ready_at = datetime.now(UTC)

    sig = signals(daily, funding)
    last = sig.index[-1]
    gaps = missing_days(daily.index, start, last.date())
    added: list[dict] = []
    problems: list[str] = []
    if book is not None:
        added, problems = book.advance(spot_daily, spot_hourly, daily, perp_hourly, funding, est)

    state = band_state(sig["close"], signed=False)
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    w = float(state.iloc[-1] * fund.iloc[-1])
    weight = None if w != w else float(w)
    atr = atr_wilder(daily["high"].to_numpy(), daily["low"].to_numpy(), daily["close"].to_numpy(), 14)[-1]
    atr14 = None if atr != atr else float(atr)
    t = sig.iloc[-1]
    band_note = (f"band ±{BAND:.0%}: ราคาปิด {t['close']:,.2f}, SMA100 {t['sma100']:,.2f}, "
                 f"เข้าเมื่อ > {t['sma100'] * (1 + BAND):,.2f} ออกเมื่อ < {t['sma100'] * (1 - BAND):,.2f}, "
                 f"สถานะ {'ถือ' if state.iloc[-1] == 1 else 'ไม่ถือ'} → {target_th(weight)}") if weight is not None else ""
    notes = missing_inputs(sig) if weight is None else (events(sig) or ["ตามสัญญาณ ณ ราคาปิด"]) + [band_note]
    if atr14 is not None:
        notes = notes + [f"ATR14 {atr14:,.2f}"]
    info = decision_inputs(sig, funding, est, last)
    if weight is not None and info["funding_proxy_days"]:
        notes = notes + [f"funding {info['funding_proxy_days']}/7 วันเป็นค่าประมาณจาก premium index"]
    if book is None:
        if weight is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดพอร์ตไม่ได้: " + ", ".join(notes))
        if last.date() not in set(spot_daily.index.date):
            raise SystemExit(f"ยังไม่มีราคา spot ของวันที่ {last.date()} รอรอบถัดไป")
        book = HedgeBook()
        added = [book.open(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, atr14)]
    elif not problems:
        e = book.decide(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, atr14)
        if e:
            added.append(e)
    for e in added:
        if e["kind"] in ("open", "decision", "no_decision", "ignored_halted"):
            e.update(info)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2))
    write_journal(book, state_path.with_name("journal.md"))
    p = book.performance()
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(last.date()), stale=last.date() < end, missing_days=gaps, problems=problems,
             new_entries=added, prices="BTCUSDT spot (spot leg, buy & hold) and USD-M perpetual (signals, perp leg, funding)",
             funding_policy=FUNDING_POLICY["version"], funding_source=info["funding_source"],
             funding_proxy_days=info["funding_proxy_days"],
             funding_estimated_days=[str(d.date()) for d, v in est.items() if v])
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print()
    print(render_th(p, [e for e in added if e["kind"] != "open"], problems))


if __name__ == "__main__":
    main()
