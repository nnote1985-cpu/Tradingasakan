"""Advance the Phase 5 path A paired run pathA-p5-r1 (simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade_p5.py [--state PATH] [--mock ALLOW|VETO]

docs/phase-5-paired-plan.md. Inputs, signals and timing are those of scripts/paper_trade_v12.py
(pathA-v1.2). The Code arm takes the Code target; the AI arm asks the locked filter
(configs/analysis/pathA_p5_filter.yaml) before any increase, and once a day records a diagnostic
answer that changes nothing. The run does not open without ANTHROPIC_API_KEY in the environment;
--mock is for dry runs on a copied state only. State lives under
/mnt/project-files/paper-trading/pathA-p5-r1/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading.alerts.live_data import FUNDING_POLICY, daily_funding, daily_klines, missing_days, spot_daily_klines, spot_hourly_klines
from trading.alerts.risk_check import events, missing_inputs, signals
from trading.analysis import pathA_filter as pf
from trading.shadow.paired_book import EXPERIMENT_ID, PairedBook, _target_of

RULES_VERSION = EXPERIMENT_ID
SYMBOL = "BTCUSDT"
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{EXPERIMENT_ID}/state.json"
KINDS = {"open": "เปิดพอร์ต", "decision": "เปลี่ยนเป้า", "trade": "ซื้อขาย", "no_decision": "ข้อมูลไม่พอ", "alert": "แจ้งเตือน",
         "halt": "หยุด (HALT)", "ignored_halted": "ไม่ทำตาม (หยุดอยู่)", "filtered": "AI ไม่ให้เพิ่ม"}

_spec = importlib.util.spec_from_file_location("paper_trade_v11", Path(__file__).with_name("paper_trade.py"))
assert _spec and _spec.loader
_v11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v11)
decision_inputs = _v11.decision_inputs


class NoKey:
    """Stands in when the key is missing on an open run: every question fails closed and costs nothing."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def ask(self, *a: object) -> pf.ProviderReply:
        return pf.ProviderReply(None, error="PROVIDER_ERROR:NO_API_KEY")


def drawdown(book) -> float:
    h = book.ai.history
    hwm = book.ai.risk.high_water_mark
    if not h or not hwm:
        return 0.0
    return max(0.0, 1 - float(h[-1]["economic_equity"]) / float(hwm))


def write_journal(book: PairedBook, path: Path) -> None:
    lines = [f"# สมุดบันทึกการทดลอง {EXPERIMENT_ID}: Code เทียบ AI (เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             f"เริ่ม {book.opened_on} สิ้นสุด {book.endpoint}; เวลาเป็น UTC; docs/phase-5-paired-plan.md", "",
             "| พอร์ต | ราคาปิดวันที่ | เวลา | ประเภท | สัดส่วน BTC | รายละเอียด |", "| --- | --- | --- | --- | --- | --- |"]
    for e in book.journal:
        w = e.get("weight")
        detail = "; ".join(e.get("notes", [])) or e.get("alert") or e.get("reason") or ""
        if e["kind"] == "trade":
            detail = f"{e['side']} {float(e['qty']):.6f} BTC @ {float(e['price']):,.2f}"
        if e["kind"] == "filtered":
            detail = f"{e['decision']} {e['reason_code']} (Code ต้องการ {e['code_weight']:.0%})"
        lines.append(f"| {e['arm']} | {e.get('decided_on', e.get('date', ''))} | {e.get('at', e.get('time', e.get('decided_at', '')))} "
                     f"| {KINDS.get(e['kind'], e['kind'])} | {'' if w is None else f'{w:.0%}'} | {detail} |")
    path.write_text("\n".join(lines) + "\n")


def render_th(p: dict, problems: list[str]) -> str:
    c, a = p["code"], p["ai"]
    if not c.get("days"):
        out = [f"การทดลอง {EXPERIMENT_ID} ยังไม่มีวันที่ปิดหลังเปิดพอร์ต"]
    else:
        out = [f"การทดลอง {EXPERIMENT_ID} ({c['since']} ถึง {c['through']}, {c['days']} วัน, สิ้นสุด {p['endpoint']})",
               f"- Code: {c['equity']:,.2f} USDT ({c['return']:+.2%}), ลงหนักสุด {c['max_dd']:.1%}",
               f"- AI: {a['equity']:,.2f} USDT ({a['return']:+.2%}), หลังหักค่า AI {a['economic_equity']:,.2f} USDT",
               f"- ซื้อ BTC ถือเฉยๆ: {c['buy_and_hold']:,.2f} USDT ({c['bh_return']:+.2%}); เงินสด: {p['cash']:,.2f} USDT"]
    out.append(f"- คำถามถึง AI: กรอง {p['filter_questions']} ครั้ง {p['filter_outcomes']}, ความเห็นรายวัน {p['diagnostic_questions']} ครั้ง, "
               f"ค่าใช้จ่าย {Decimal(p['spent_usd']):.4f} USD")
    out += [f"- ปัญหา: {x}" for x in problems]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--mock", choices=("ALLOW", "VETO"), help="dry run with recorded answers (never on the real state)")
    args = ap.parse_args()
    if args.mock and Path(args.state) == Path(DEFAULT_STATE):
        raise SystemExit("--mock is only for a copied state: the experiment's evidence never uses mock answers")
    graph = pf.load_filter_graph()
    model = graph.role("filter").model_id
    assert model is not None
    if args.mock:
        provider: pf.Provider = pf.MockProvider(args.mock)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        provider = pf.AnthropicProvider(model)
    else:
        provider = NoKey(model)

    started = datetime.now(UTC)
    end = started.date() - timedelta(days=1)
    state_path = Path(args.state)
    book = PairedBook(json.loads(state_path.read_text())) if state_path.exists() else None
    if book is None and isinstance(provider, NoKey):
        raise SystemExit("no ANTHROPIC_API_KEY in the environment: the paired run does not open without the AI")

    start = end - timedelta(days=400)
    daily = daily_klines(SYMBOL, start, end)
    funding, est = daily_funding(SYMBOL, end - timedelta(days=40), end)
    first = (datetime.fromisoformat(book.last_day) + timedelta(days=1)).date() if book and book.last_day else end
    spot_daily = spot_daily_klines(SYMBOL, first - timedelta(days=1), end)
    spot_hourly = spot_hourly_klines(SYMBOL, first, end) if book and first <= end else spot_daily.iloc[:0]
    data_ready_at = datetime.now(UTC)

    sig = signals(daily, funding)
    last = sig.index[-1]
    gaps = missing_days(daily.index, start, last.date())
    added: list[dict] = []
    problems: list[str] = []
    if book is not None:
        added, problems = book.advance(spot_daily, spot_hourly)

    w = sig["suggested_btc_weight"].iloc[-1]
    weight = None if w != w else float(w)
    notes = missing_inputs(sig) if weight is None else (events(sig) or ["ตามสัญญาณ ณ ราคาปิด"])
    info = decision_inputs(sig, funding, est, last)
    closes = [(str(d.date()), float(c)) for d, c in daily["close"].iloc[-30:].items()]

    def ask(kind: str, proposal_id: str, current: float, target: float) -> dict:
        assert book is not None
        snap = pf.snapshot(str(last.date()), info["inputs"], closes, _target_of(book.ai), drawdown(book))
        return pf.ask(kind, proposal_id, snap, current, target, provider, graph,
                      book.budget_left(datetime.now(UTC).strftime("%Y-%m")), lambda: datetime.now(UTC))

    if book is None:
        if weight is None:
            raise SystemExit("ข้อมูลไม่พอ ยังเปิดการทดลองไม่ได้: " + ", ".join(notes))
        if last.date() not in set(spot_daily.index.date):
            raise SystemExit(f"ยังไม่มีราคา spot ของวันที่ {last.date()} รอรอบถัดไป")
        book = PairedBook()
        added = book.open(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, ask)
    elif not problems:
        added += book.decide(str(last.date()), weight, data_ready_at, datetime.now(UTC), notes, ask)
    for e in added:
        if e["kind"] in ("open", "decision", "no_decision", "ignored_halted", "filtered"):
            e.update(info)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2))
    write_journal(book, state_path.with_name("journal.md"))
    p = book.performance()
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(last.date()), stale=last.date() < end, missing_days=gaps, problems=problems,
             new_entries=added, provider=provider.model_id, prices="BTCUSDT spot (fills, marks, buy & hold); signals from perp + funding",
             funding_policy=FUNDING_POLICY["version"], funding_source=info["funding_source"],
             funding_proxy_days=info["funding_proxy_days"])
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print()
    print(render_th(p, problems))


if __name__ == "__main__":
    main()
