"""Advance the forward futures-r11 book (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade_r11.py [--state PATH]

docs/band-books-plan.md. This is scripts/paper_trade_r7.py (futures-r7) with one change: the
side is the R11 rule, a ±2% band latch on SMA100 (trading.strategies.sma_band.band_state).
Perp 1x, signals, fills, marks, funding, costs and the spot.v1 levels are r7's. A HALT closes
the position and waits for a resume record; this script never writes one. State lives under
/mnt/project-files/paper-trading/futures-r11/.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading.alerts.live_data import daily_funding, daily_klines, hourly_klines, missing_days
from trading.shadow.band_books import R11_RULES_VERSION as RULES_VERSION
from trading.shadow.band_books import BandPerpBook as TrendPerpBook
from trading.strategies.sma_band import BAND, SMA_N, band_state

SYMBOL = "BTCUSDT"
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json"
SIDE_TH = {1: "long", -1: "short", 0: "ไม่มีสถานะ"}
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนฝั่ง", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ",
         "alert": "แจ้งเตือน", "halt": "หยุด (HALT)", "ignored_halted": "ไม่ทำตาม (หยุดอยู่)", "resume": "เริ่มใหม่"}


def write_journal(book: TrendPerpBook, path: Path) -> None:
    lines = ["# สมุดบันทึกพอร์ตจำลอง futures-r11 (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; BTCUSDT perp 1x; แจ้งเตือนเมื่อขาดทุน 5%/วัน หรือ drawdown 15%, หยุดที่ drawdown 25%", "",
             "| ราคาปิดวันที่ | เวลา | ประเภท | ฝั่ง | รายละเอียด |", "| --- | --- | --- | --- | --- |"]
    for e in book.journal:
        side = e.get("side", e.get("target_side"))
        detail = "; ".join(e.get("notes", [])) or e.get("alert") or e.get("reason") or ""
        if e["kind"] == "trade":
            detail = f"{e['action']} {e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}"
            side = e["target_side"]
        lines.append(f"| {e.get('decided_on', e.get('date', ''))} | {e.get('at', e.get('time', e.get('decided_at', '')))} "
                     f"| {KINDS.get(e['kind'], e['kind'])} | {'' if side is None else SIDE_TH.get(side, side)} | {detail} |")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args()
    started = datetime.now(UTC)
    end = started.date() - timedelta(days=1)
    state_path = Path(args.state)
    book = TrendPerpBook(json.loads(state_path.read_text())) if state_path.exists() else None

    # Every input is fetched before any time is stamped.
    start = end - timedelta(days=SMA_N + 200)
    daily = daily_klines(SYMBOL, start, end)
    first = (datetime.fromisoformat(book.last_day) + timedelta(days=1)).date() if book and book.last_day else end
    funding, est = daily_funding(SYMBOL, min(first, end) - timedelta(days=1), end)
    hourly = hourly_klines(SYMBOL, first, end) if book and first <= end else daily.iloc[:0]
    data_ready_at = datetime.now(UTC)

    last = daily.index[-1]
    gaps = missing_days(daily.index, start, last.date())
    sig = band_state(daily["close"], signed=True)
    added: list[dict] = []
    problems: list[str] = []
    if book is not None:
        added, problems = book.advance(daily, hourly, funding, est)

    enough = daily["close"].notna().sum() >= SMA_N and not gaps
    side = int(sig.iloc[-1]) if enough else None
    sma = float(daily["close"].rolling(SMA_N).mean().iloc[-1])
    notes = ([f"band ±{BAND:.0%}: ราคาปิด {float(daily['close'].iloc[-1]):,.2f}, SMA{SMA_N} {sma:,.2f}, "
              f"long เมื่อ > {sma * (1 + BAND):,.2f} short เมื่อ < {sma * (1 - BAND):,.2f}"] if side is not None
             else [f"ข้อมูลรายวันไม่ครบ ({len(gaps)} วันหาย)"])
    if book is None:
        if side is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดพอร์ตไม่ได้: " + ", ".join(notes))
        book = TrendPerpBook()
        added = [book.open(str(last.date()), side, data_ready_at, datetime.now(UTC), notes)]
    elif not problems:
        e = book.decide(str(last.date()), side, data_ready_at, datetime.now(UTC), notes)
        if e:
            added.append(e)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2))
    write_journal(book, state_path.with_name("journal.md"))
    p = book.performance()
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(last.date()), stale=last.date() < end, missing_days=gaps, problems=problems,
             new_entries=added, prices="BTCUSDT USD-M perpetual (signals, fills, marks, funding, buy & hold)",
             funding_estimated_days=[str(d.date()) for d, v in est.items() if v])
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print()
    s = SIDE_TH.get(book.side, book.side)
    if p["days"]:
        print(f"พอร์ตจำลอง futures-r11 {p['since']} ถึง {p['through']} ({p['days']} วัน): {p['equity']:,.0f} USDT "
              f"({p['return']:+.1%}) เทียบซื้อถือ {p['buy_and_hold']:,.0f} ({p['bh_return']:+.1%}), ถือ {s}")
    else:
        print(f"พอร์ตจำลอง futures-r11 เปิดแล้ว รอวันที่ปิดแรก เป้าหมาย {SIDE_TH.get(added[0]['side']) if added else s}")
    for x in problems:
        print(f"- ปัญหา: {x}")


if __name__ == "__main__":
    main()
