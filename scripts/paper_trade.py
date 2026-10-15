"""Advance the forward paper-trading book (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade.py [--state PATH] [--end YYYY-MM-DD]

First run opens the book on the latest closed day; each later run trades every
newly closed day at its open with the weight decided the close before.
State and a readable journal live next to each other (default: the shared
project folder, so every session sees the same book).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from trading.alerts.live_data import daily_funding, daily_klines
from trading.alerts.risk_check import events, signals
from trading.paper.ledger import advance, new_state, performance, render_th

SYMBOL = "BTCUSDT"
DEFAULT_STATE = "/mnt/project-files/paper-trading/state.json"


def write_journal(state: dict, path: Path) -> None:
    lines = ["# สมุดบันทึกพอร์ตจำลอง (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "| วันที่ (UTC) | ประเภท | สัดส่วน BTC | รายละเอียด |", "| --- | --- | --- | --- |"]
    kinds = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย"}
    for e in state["journal"]:
        lines.append(f"| {e['date']} | {kinds.get(e['kind'], e['kind'])} | {e['weight']:.0%} | {'; '.join(e['notes'])} |")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--end", help="last UTC day to include (default: yesterday)")
    args = ap.parse_args()
    end = date.fromisoformat(args.end) if args.end else datetime.now(timezone.utc).date() - timedelta(days=1)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else None

    daily = daily_klines(SYMBOL, end - timedelta(days=400), end)
    funding, _ = daily_funding(SYMBOL, end - timedelta(days=40), end)
    sig = signals(daily, funding)
    last = sig.index[-1]

    if state is None:
        state = new_state(str(last.date()), float(sig["suggested_btc_weight"].iloc[-1]),
                          events(sig) or ["เปิดตามสัญญาณ ณ ราคาปิดวันนี้"])
        added = state["journal"][:]
    else:
        start = sig.index > state["last_day"]
        ev = {str(d.date()): events(sig.loc[:d]) for d in sig.index[start]}
        added = advance(state, daily, sig, ev)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    write_journal(state, state_path.with_name("journal.md"))
    p = performance(state)
    p["data_through"] = str(last.date())
    p["stale"] = last.date() < end
    p["new_entries"] = added
    print(json.dumps(p, ensure_ascii=False, indent=2))
    print()
    print(render_th(p, [e for e in added if e["kind"] != "open"]))


if __name__ == "__main__":
    main()
