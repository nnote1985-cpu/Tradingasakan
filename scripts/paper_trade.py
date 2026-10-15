"""Advance the forward paper-trading book (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade.py [--state PATH]

Live only: every decision is stamped with the time this run received the data,
so there is no --end for replaying the past. Each run (1) fills the pending
order if its 1h bar is now in the archive and marks newly closed days, then
(2) decides on the latest close the book has reached. State and a readable
journal live side by side (default: the shared project folder).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading.alerts.live_data import daily_funding, daily_klines, hourly_klines, missing_days
from trading.alerts.risk_check import events, missing_inputs, signals
from trading.paper.ledger import VERSION, advance, decide, new_state, performance, render_th

SYMBOL = "BTCUSDT"
DEFAULT_STATE = "/mnt/project-files/paper-trading/state.json"
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ"}


def migrate_v1(old: dict, written_at: datetime) -> dict:
    """The first book (version 1) was opened before receipt-time fills existed and
    never traded; reopen it on the same close, stamped with when that run saved it."""
    if old["history"] or old["trades"]:
        raise SystemExit("state.json v1 already has trades; migrate it by hand")
    notes = [n for e in old["journal"] for n in e["notes"]] + ["ย้ายมาใช้กติกาเติมคำสั่งหลังเวลารับข้อมูล"]
    return new_state(old["last_day"], old["target_weight"], written_at, notes, old["start_equity"])


def write_journal(state: dict, path: Path) -> None:
    lines = ["# สมุดบันทึกพอร์ตจำลอง (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; คำสั่งเติมที่ราคาเปิดของแท่ง 1 ชม. แรกหลังเวลาที่ได้รับข้อมูล", "",
             "| ราคาปิดวันที่ | เวลา | ประเภท | สัดส่วน BTC | รายละเอียด |", "| --- | --- | --- | --- | --- |"]
    for e in state["journal"]:
        lines.append(f"| {e['date']} | {e.get('at', '')} | {KINDS.get(e['kind'], e['kind'])} | {e['weight']:.0%} "
                     f"| {'; '.join(e['notes'])} |")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    end = now.date() - timedelta(days=1)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state is not None and state.get("version") != VERSION:
        state = migrate_v1(state, datetime.fromtimestamp(state_path.stat().st_mtime, timezone.utc))

    start = end - timedelta(days=400)
    daily = daily_klines(SYMBOL, start, end)
    funding, est = daily_funding(SYMBOL, end - timedelta(days=40), end)
    sig = signals(daily, funding)
    last = sig.index[-1]
    gaps = missing_days(daily.index, start, last.date())

    added, problems = [], []
    if state is not None:
        first = (datetime.fromisoformat(state["last_day"]) + timedelta(days=1)).date()
        hourly = hourly_klines(SYMBOL, first, end) if first <= end else daily.iloc[:0]
        added, problems = advance(state, daily, hourly)

    w = sig["suggested_btc_weight"].iloc[-1]
    weight = None if w != w else float(w)
    notes = missing_inputs(sig) if weight is None else (events(sig) or ["ตามสัญญาณ ณ ราคาปิด"])
    if state is None:
        if weight is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดพอร์ตไม่ได้: " + ", ".join(notes))
        state = new_state(str(last.date()), weight, now, notes)
        added = state["journal"][:]
    elif not problems:
        e = decide(state, str(last.date()), weight, now, notes)
        if e:
            added.append(e)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    write_journal(state, state_path.with_name("journal.md"))
    p = performance(state)
    p.update(run_at=now.isoformat(), data_through=str(last.date()), stale=last.date() < end,
             missing_days=gaps, problems=problems, new_entries=added)
    print(json.dumps(p, ensure_ascii=False, indent=2))
    print()
    print(render_th(p, [e for e in added if e["kind"] != "open"], problems))


if __name__ == "__main__":
    main()
