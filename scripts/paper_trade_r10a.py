"""Advance the forward pathA-r10a book (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade_r10a.py [--state PATH]

docs/band-books-plan.md. This is scripts/paper_trade_v12.py (pathA-v1.2) with one change:
the BTC weight is the R10a rule, a ±2% band latch on SMA100
(trading.strategies.sma_band.band_state) times path A's funding factor. Inputs, decision
timing, spot fills and marks, costs and the spot.v1 limits are v1.2's. A HALT sells to cash
and waits for a resume record; this script never writes one. State lives under
/mnt/project-files/paper-trading/pathA-r10a/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading.alerts.live_data import FUNDING_POLICY, daily_funding, daily_klines, missing_days, spot_daily_klines, spot_hourly_klines
from trading.alerts.risk_check import events, missing_inputs, signals
from trading.shadow.band_books import R10A_RULES_VERSION as RULES_VERSION
from trading.shadow.band_books import BandSpotBook as PathABook
from trading.strategies.sma_band import BAND, band_state

SYMBOL = "BTCUSDT"
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json"
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ",
         "alert": "แจ้งเตือน", "halt": "หยุด (HALT)", "ignored_halted": "ไม่ทำตาม (หยุดอยู่)", "resume": "เริ่มใหม่"}

# Same decision-input snapshot as pathA-v1.1 (reused, not copied, so the books record identical inputs).
_spec = importlib.util.spec_from_file_location("paper_trade_v11", Path(__file__).with_name("paper_trade.py"))
assert _spec and _spec.loader
_v11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v11)
decision_inputs = _v11.decision_inputs


def write_journal(book: PathABook, path: Path) -> None:
    lines = ["# สมุดบันทึกพอร์ตจำลอง pathA-r10a (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; เกณฑ์ spot.v1: แจ้งเตือนเมื่อขาดทุน 5%/วัน หรือ drawdown 15%, หยุดที่ drawdown 25%", "",
             "| ราคาปิดวันที่ | เวลา | ประเภท | สัดส่วน BTC | รายละเอียด |", "| --- | --- | --- | --- | --- |"]
    for e in book.journal:
        w = e.get("weight")
        detail = "; ".join(e.get("notes", [])) or e.get("alert") or e.get("reason") or ""
        if e["kind"] == "trade":
            detail = f"{e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}"
        lines.append(f"| {e.get('decided_on', e.get('date', ''))} | {e.get('at', e.get('time', e.get('decided_at', '')))} "
                     f"| {KINDS.get(e['kind'], e['kind'])} | {'' if w is None else f'{w:.0%}'} | {detail} |")
    path.write_text("\n".join(lines) + "\n")


def render_th(p: dict, added: list[dict], problems: list[str]) -> str:
    lines = []
    if p["days"]:
        lines += [f"พอร์ตจำลอง pathA-r10a {p['since']} ถึง {p['through']} ({p['days']} วัน)",
                  f"- โปรแกรม: {p['equity']:,.0f} USDT ({p['return']:+.1%}), ลงหนักสุด {p['max_dd']:.1%}, ซื้อขาย {p['trades']} ครั้ง",
                  f"- ซื้อ BTC ถือเฉยๆ: {p['buy_and_hold']:,.0f} USDT ({p['bh_return']:+.1%}), ลงหนักสุด {p['bh_max_dd']:.1%}",
                  f"- ถือ BTC อยู่ {p['weight_held']:.0%}"]
    else:
        lines.append("พอร์ตจำลอง pathA-r10a ยังไม่มีวันที่ปิดหลังซื้อขาย")
    if p.get("halted"):
        lines.append(f"- หยุดอยู่ (HALT {p.get('halt_reason')}) รอ Note สั่งเริ่มใหม่")
    for e in added:
        if e["kind"] == "decision":
            lines.append(f"- ตัดสินใจจากราคาปิด {e['decided_on']}: เปลี่ยนเป็น {e['weight']:.0%} ({', '.join(e['notes'])})")
        elif e["kind"] == "trade":
            lines.append(f"- {e['at']}: {e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}")
        elif e["kind"] == "alert":
            lines.append(f"- แจ้งเตือน {e['alert']} ที่ {e['time']}")
        elif e["kind"] == "halt":
            lines.append(f"- HALT {e['reason']}: ขาย BTC ทั้งหมดที่ {e['fill_at']}")
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
    book = PathABook(json.loads(state_path.read_text())) if state_path.exists() else None

    # Every input is fetched before any time is stamped.
    start = end - timedelta(days=400)
    daily = daily_klines(SYMBOL, start, end)                       # perp: signals
    funding, est = daily_funding(SYMBOL, end - timedelta(days=40), end)
    first = (datetime.fromisoformat(book.last_day) + timedelta(days=1)).date() if book and book.last_day else end
    spot_daily = spot_daily_klines(SYMBOL, first - timedelta(days=1), end)      # spot: marks
    spot_hourly = spot_hourly_klines(SYMBOL, first, end) if book and first <= end else spot_daily.iloc[:0]
    data_ready_at = datetime.now(UTC)

    sig = signals(daily, funding)
    last = sig.index[-1]
    gaps = missing_days(daily.index, start, last.date())
    added: list[dict] = []
    problems: list[str] = []
    if book is not None:
        added, problems = book.advance(spot_daily, spot_hourly)

    state = band_state(sig["close"], signed=False)
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)     # NaN when funding is missing
    w = float(state.iloc[-1] * fund.iloc[-1])
    weight = None if w != w else float(w)
    t = sig.iloc[-1]
    band_note = (f"band ±{BAND:.0%}: ราคาปิด {t['close']:,.2f}, SMA100 {t['sma100']:,.2f}, "
                 f"เข้าเมื่อ > {t['sma100'] * (1 + BAND):,.2f} ออกเมื่อ < {t['sma100'] * (1 - BAND):,.2f}, "
                 f"สถานะ {'ถือ' if state.iloc[-1] == 1 else 'ไม่ถือ'}") if weight is not None else ""
    notes = missing_inputs(sig) if weight is None else (events(sig) or ["ตามสัญญาณ ณ ราคาปิด"]) + [band_note]
    info = decision_inputs(sig, funding, est, last)
    if weight is not None and info["funding_proxy_days"]:
        notes = notes + [f"funding {info['funding_proxy_days']}/7 วันเป็นค่าประมาณจาก premium index"]
    if book is None:
        if weight is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดพอร์ตไม่ได้: " + ", ".join(notes))
        if last.date() not in set(spot_daily.index.date):
            raise SystemExit(f"ยังไม่มีราคา spot ของวันที่ {last.date()} รอรอบถัดไป")
        book = PathABook()
        added = [book.open(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes)]
    elif not problems:
        e = book.decide(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes)
        if e:
            added.append(e)
    for e in added:     # the decision's input snapshot, as in pathA-v1.1
        if e["kind"] in ("open", "decision", "no_decision", "ignored_halted"):
            e.update(info)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2))
    write_journal(book, state_path.with_name("journal.md"))
    p = book.performance()
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(last.date()), stale=last.date() < end, missing_days=gaps, problems=problems,
             new_entries=added, prices="BTCUSDT spot (fills, marks, buy & hold); signals from perp + funding",
             funding_policy=FUNDING_POLICY["version"], funding_source=info["funding_source"],
             funding_proxy_days=info["funding_proxy_days"])
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print()
    print(render_th(p, [e for e in added if e["kind"] != "open"], problems))


if __name__ == "__main__":
    main()
