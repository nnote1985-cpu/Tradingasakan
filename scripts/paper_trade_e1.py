"""Advance the forward spot-e1-v1 book (R13 E1 Donchian_Breakout; simulated money, no exchange orders).

    SSL_CERT_FILE=... uv run python scripts/paper_trade_e1.py [--state PATH]

docs/e1-paper-book-plan.md. The book is a replay: every run fetches perp 1h (signals, from
start - 120 days) and spot 1h/1m (fills and marks, from start) up to yesterday, replays the frozen
rule (trading.strategies.argus_e1.replay) over whole days only, checks that every trade already
recorded comes out the same, and appends what is new. The first run only records the start day.
State lives under /mnt/project-files/paper-trading/spot-e1-v1/.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from trading.alerts.live_data import SPOT_BASE, _klines, hourly_klines, spot_hourly_klines
from trading.strategies.argus_e1 import RULES_VERSION, replay

SYMBOL = "BTCUSDT"
WARMUP_DAYS = 120
EQUITY0 = 10_000.0
DEFAULT_STATE = f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json"
KINDS = {"open": "ซื้อ", "close": "ขาย", "stop_moved": "เลื่อน stop", "alert": "แจ้งเตือน", "halt": "หยุด (HALT)"}
REASONS = {"stop": "โดน stop", "target": "ถึงเป้า", "halt": "HALT"}


def jsonable(x):
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [jsonable(v) for v in x]
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    if hasattr(x, "item"):
        return x.item()
    return x


def complete_through(h1: pd.DataFrame, m1: pd.DataFrame, sig: pd.DataFrame, first: date, last: date) -> date | None:
    """Last day d (first <= d <= last) such that every day from first to d has 24 spot hours,
    1,440 spot minutes and 24 perp hours. None if ``first`` itself is incomplete."""
    def counts(df):
        return df.groupby(df.index.floor("1D")).size() if len(df) else pd.Series(dtype=int)
    ch, cm, cs = counts(h1), counts(m1), counts(sig)
    ok = None
    d = first
    while d <= last:
        k = pd.Timestamp(d, tz="UTC")
        if ch.get(k, 0) != 24 or cm.get(k, 0) != 1440 or cs.get(k, 0) != 24:
            break
        ok, d = d, d + timedelta(days=1)
    return ok


def max_dd(s: pd.Series) -> float:
    return float((1 - s / s.cummax()).max()) if len(s) else 0.0


def performance(r: dict, h1: pd.DataFrame, start: pd.Timestamp, through: date) -> dict:
    eq = r["equity_daily"]
    last_px = float(h1["close"].iloc[-1])
    equity = r["cash"] + r["qty"] * last_px
    bh_qty = EQUITY0 * (1 - 0.0010) / (float(h1["open"].iloc[0]) * 1.0002)
    bh = (bh_qty * h1["close"])
    bh_daily = bh[bh.index.hour == 23]
    return {"since": str(start.date()), "through": str(through), "days": int(len(eq) - 1) if len(eq) else 0,
            "equity": equity, "return": equity / EQUITY0 - 1, "max_dd": max_dd(eq),
            "trades": len(r["trades"]), "open_position": r["open_position"] is not None,
            "weight_held": r["qty"] * last_px / equity if equity else 0.0,
            "buy_and_hold": float(bh.iloc[-1]), "bh_return": float(bh.iloc[-1]) / EQUITY0 - 1,
            "bh_max_dd": max_dd(pd.concat([pd.Series([EQUITY0]), bh_daily.reset_index(drop=True)])),
            "halted": r["halted"]}


def describe(e: dict) -> str:
    if e["kind"] == "open":
        return f"ซื้อ {e['qty']:.6f} BTC @ {e['price']:,.2f}, stop {e['stop']:,.2f}, เป้า {e['target']:,.2f}"
    if e["kind"] == "close":
        return f"ขาย @ {e['price']:,.2f} ({REASONS.get(e['reason'], e['reason'])}), พอร์ต {e['equity']:,.0f} USDT"
    if e["kind"] == "stop_moved":
        return f"เลื่อน stop เป็น {e['stop']:,.2f} (ยกเลิกเป้า)"
    if e["kind"] == "halt":
        return f"drawdown ถึง 25% ที่ {e['equity']:,.0f} USDT หยุดพอร์ต"
    return e.get("alert", "")


def write_journal(events: list[dict], path: Path) -> None:
    lines = [f"# สมุดบันทึกพอร์ตจำลอง {RULES_VERSION} (R13 E1 Donchian, เงินสมมติ ไม่มีคำสั่งซื้อขายจริง)", "",
             "เวลาเป็น UTC; สัญญาณจาก perp 1h รวมเป็น H4, ซื้อขายด้วยราคา spot; spot.v1: แจ้งเตือน 5%/วัน หรือ drawdown 15%, หยุดที่ 25%", "",
             "| เวลา | ประเภท | รายละเอียด |", "| --- | --- | --- |"]
    lines += [f"| {e['time']} | {KINDS.get(e['kind'], e['kind'])} | {describe(e)} |" for e in events]
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args()
    started = datetime.now(UTC)
    yesterday = started.date() - timedelta(days=1)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else None

    if state is None:
        start_day = started.date()
        state = {"rules_version": RULES_VERSION, "start": str(start_day), "created_at": started.isoformat(),
                 "through": None, "trades": [], "events": [], "halted": False}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(json.dumps({"rules_version": RULES_VERSION, "new_entries": [], "start": str(start_day), "problems": []}, ensure_ascii=False))
        print()
        print(f"เปิดพอร์ตจำลอง {RULES_VERSION}: เริ่มนับตั้งแต่ {start_day} 00:00 UTC เงินตั้งต้น 10,000 USDT (ยังไม่มีการซื้อขาย)")
        return
    if state.get("rules_version") != RULES_VERSION:
        raise SystemExit(f"state file belongs to {state.get('rules_version')}, not {RULES_VERSION}")

    start_day = date.fromisoformat(state["start"])
    if yesterday < start_day:
        print(json.dumps({"rules_version": RULES_VERSION, "new_entries": [], "problems": [], "stale": False}, ensure_ascii=False))
        print()
        print(f"พอร์ตจำลอง {RULES_VERSION} ยังไม่ถึงวันแรก ({start_day})")
        return

    # Every input is fetched before any time is stamped.
    sig = hourly_klines(SYMBOL, start_day - timedelta(days=WARMUP_DAYS), yesterday)      # perp: signals
    h1 = spot_hourly_klines(SYMBOL, start_day, yesterday)                                  # spot: fills, marks
    m1 = _klines(SYMBOL, "1m", start_day, yesterday, SPOT_BASE)                            # spot: stops, targets
    data_ready_at = datetime.now(UTC)

    through = complete_through(h1, m1, sig[sig.index >= pd.Timestamp(start_day, tz="UTC")], start_day, yesterday)
    problems: list[str] = []
    if through is None:
        print(json.dumps({"rules_version": RULES_VERSION, "new_entries": [], "problems": [], "stale": True,
                          "data_ready_at": data_ready_at.isoformat()}, ensure_ascii=False))
        print()
        print(f"พอร์ตจำลอง {RULES_VERSION}: ข้อมูลวันที่ {start_day} ยังไม่ครบ รอรอบถัดไป")
        return
    start = pd.Timestamp(start_day, tz="UTC")
    end = pd.Timestamp(through, tz="UTC") + pd.Timedelta(days=1)
    sig, h1, m1 = sig[sig.index < end], h1[h1.index < end], m1[m1.index < end]
    r = replay(sig, h1, m1, start, end, equity0=EQUITY0)

    trades = jsonable(r["trades"])
    events = jsonable(r["events"])
    old_trades, old_events = state["trades"], state["events"]
    if trades[: len(old_trades)] != old_trades:
        problems.append("replay ให้รายการซื้อขายที่บันทึกไว้แล้วไม่ตรงเดิม (ไม่เขียนทับ state)")
    old_keys = {json.dumps(e, sort_keys=True) for e in old_events}
    added = [e for e in events if json.dumps(e, sort_keys=True) not in old_keys]

    p = performance(r, h1, start, through)
    if not problems:
        state.update(through=str(through), trades=trades, events=events, halted=r["halted"],
                     open_position=jsonable(r["open_position"]), last_run_at=started.isoformat())
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        write_journal(events, state_path.with_name("journal.md"))
    p.update(rules_version=RULES_VERSION, run_started_at=started.isoformat(), data_ready_at=data_ready_at.isoformat(),
             data_through=str(through), stale=through < yesterday, problems=problems, new_entries=added,
             prices="signals: BTCUSDT perp 1h -> H4; fills, stops, marks, buy & hold: BTCUSDT spot")
    print(json.dumps(jsonable(p), ensure_ascii=False, indent=2, default=str))
    print()
    lines = [f"พอร์ตจำลอง {RULES_VERSION} (E1 Donchian) {p['since']} ถึง {p['through']} ({p['days']} วัน)",
             f"- โปรแกรม: {p['equity']:,.0f} USDT ({p['return']:+.1%}), ลงหนักสุด {p['max_dd']:.1%}, ปิดเทรดแล้ว {p['trades']} ครั้ง",
             f"- ซื้อ BTC ถือเฉยๆ: {p['buy_and_hold']:,.0f} USDT ({p['bh_return']:+.1%}), ลงหนักสุด {p['bh_max_dd']:.1%}",
             f"- ถือ BTC อยู่ {p['weight_held']:.0%}" + (" (หยุดอยู่ HALT)" if p["halted"] else "")]
    lines += [f"- {e['time']}: {describe(e)}" for e in added]
    lines += [f"- ปัญหา: {x}" for x in problems]
    if p["stale"]:
        lines.append(f"- ข้อมูลครบถึง {through} เท่านั้น")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
