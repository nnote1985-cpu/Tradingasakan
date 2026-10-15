"""Advance the forward paper-trading book (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade.py [--state PATH]

The book under test runs from a frozen git tag named after RULES_VERSION
(see docs/alerts.md), so development on the branch never changes its
behaviour. Each rules version keeps its own state under
/mnt/project-files/paper-trading/<RULES_VERSION>/; a state file stamped with
another version is refused.

Live only, so there is no --end for replaying the past. Each run fetches every
input first (perp closes and funding for signals, spot bars for prices) and
stamps data_ready_at; then (1) fills the pending order if its spot 1h bar is now
in the archive and marks newly closed days at the spot close, then (2) decides
on the latest close the book has reached and stamps decided_at once the decision
is computed. Orders fill at the first 1h bar starting after decided_at. State and
a readable journal live side by side (default: the shared project folder).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from trading.alerts.live_data import FUNDING_POLICY, daily_funding, daily_klines, missing_days, spot_daily_klines, spot_hourly_klines
from trading.alerts.risk_check import events, missing_inputs, signals
from trading.paper.ledger import RULES_VERSION, VERSION, advance, decide, new_state, performance, render_th

SYMBOL = "BTCUSDT"
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json"
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ"}


def write_journal(state: dict, path: Path) -> None:
    lines = ["# สมุดบันทึกพอร์ตจำลอง (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; คำสั่งเติมที่ราคาเปิด (spot) ของแท่ง 1 ชม. แรกหลังเวลาที่ตัดสินใจเสร็จ", "",
             "| ราคาปิดวันที่ | เวลา | ประเภท | สัดส่วน BTC | รายละเอียด |", "| --- | --- | --- | --- | --- |"]
    for e in state["journal"]:
        lines.append(f"| {e['date']} | {e.get('at', '')} | {KINDS.get(e['kind'], e['kind'])} | {e['weight']:.0%} "
                     f"| {'; '.join(e['notes'])} |")
    path.write_text("\n".join(lines) + "\n")


def decision_inputs(sig, funding, est, last) -> dict:
    """Everything the decision used, frozen into the journal entry (never recomputed later)."""
    days = [last - timedelta(days=i) for i in range(6, -1, -1)]
    window = [{"day": str(d.date()), "funding": None if d not in funding.index else float(funding.loc[d]),
               "source": "proxy" if bool(est.get(d, False)) else ("actual" if d in funding.index else "missing")}
              for d in days]
    n = sum(1 for w in window if w["source"] == "proxy")
    row = sig.loc[last]

    def val(x):
        if x is None or (isinstance(x, float) and x != x):
            return None
        return bool(x) if isinstance(x, (bool, np.bool_)) else float(x)

    snap = {k: val(row[c]) for k, c in (("close_perp", "close"), ("sma100", "sma100"), ("trend_up", "trend_up"),
                                         ("funding_7d_per_day", "funding_7d_per_day"), ("funding_hot", "funding_hot"),
                                         ("suggested_btc_weight", "suggested_btc_weight"))}
    snap["funding_window"] = window
    return {"funding_policy": FUNDING_POLICY["version"], "funding_proxy_days": n,
            "funding_source": "actual archive" if n == 0 else
            f"actual archive + premium-index proxy ({n}/7 days are proxy, not actual funding)",
            "inputs": snap, "inputs_sha256": hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args()
    started = datetime.now(UTC)
    end = started.date() - timedelta(days=1)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state is not None and (state.get("version") != VERSION or state.get("rules_version") != RULES_VERSION):
        raise SystemExit(f"state.json is {state.get('rules_version')} v{state.get('version')}, this code is "
                         f"{RULES_VERSION} v{VERSION}; run the matching frozen branch or start a new book")

    # Every input is fetched before any time is stamped.
    start = end - timedelta(days=400)
    daily = daily_klines(SYMBOL, start, end)                       # perp: signals
    funding, est = daily_funding(SYMBOL, end - timedelta(days=40), end)
    first = (datetime.fromisoformat(state["last_day"]) + timedelta(days=1)).date() if state else end
    spot_daily = spot_daily_klines(SYMBOL, first - timedelta(days=1), end)      # spot: marks
    spot_hourly = spot_hourly_klines(SYMBOL, first, end) if state and first <= end else spot_daily.iloc[:0]
    data_ready_at = datetime.now(UTC)

    sig = signals(daily, funding)
    last = sig.index[-1]
    gaps = missing_days(daily.index, start, last.date())
    added, problems = [], []
    if state is not None:
        added, problems = advance(state, spot_daily, spot_hourly)

    w = sig["suggested_btc_weight"].iloc[-1]
    weight = None if w != w else float(w)
    notes = missing_inputs(sig) if weight is None else (events(sig) or ["ตามสัญญาณ ณ ราคาปิด"])
    info = decision_inputs(sig, funding, est, last)
    if weight is not None and info["funding_proxy_days"]:
        notes = notes + [f"funding {info['funding_proxy_days']}/7 วันเป็นค่าประมาณจาก premium index"]
    if state is None:
        if weight is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดพอร์ตไม่ได้: " + ", ".join(notes))
        if last.date() not in set(spot_daily.index.date):
            raise SystemExit(f"ยังไม่มีราคา spot ของวันที่ {last.date()} รอรอบถัดไป")
        state = new_state(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, info=info)
        added = state["journal"][:]
    elif not problems:
        e = decide(state, str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, info)
        if e:
            added.append(e)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    write_journal(state, state_path.with_name("journal.md"))
    p = performance(state)
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(last.date()), stale=last.date() < end, missing_days=gaps, problems=problems,
             new_entries=added, prices="BTCUSDT spot (fills, marks, buy & hold); signals from perp + funding",
             funding_policy=FUNDING_POLICY["version"], funding_source=info["funding_source"],
             funding_proxy_days=info["funding_proxy_days"])
    print(json.dumps(p, ensure_ascii=False, indent=2))
    print()
    print(render_th(p, [e for e in added if e["kind"] != "open"], problems))


if __name__ == "__main__":
    main()
