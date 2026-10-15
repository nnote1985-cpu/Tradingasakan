"""Forward paper trading for path A: simulated money only, no exchange orders.

The book is a spot book: USDT cash and BTC, no leverage, no funding. Signals come
from perpetual data (closes and funding); fills, marks and the buy & hold
benchmark use BTCUSDT spot bars.

Timing: a decision on day d's close is stamped with two times taken by the run
itself: ``data_ready_at`` (every file the decision needs has arrived) and
``decided_at`` (the decision has been computed). The order fills at the open of
the first 1h bar that starts strictly after ``decided_at``. A fill at or before
``decided_at`` is refused as a simulator error. Decisions are frozen once made,
and a buy & hold book with the same cash and costs is filled at the first fill.

The ledger never moves past a day it has not seen: if a closed day is missing
from the data, nothing is processed until it appears.
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from trading.strategies.trend_regime_v2 import RegimeCosts

START_EQUITY = 10_000.0
VERSION = 3                 # ledger file format

# Behaviour of the book under test. Bump it (and tag the commit) whenever any
# rule that changes what the book would do changes: signals, thresholds, data
# handling, timing, costs. Each value gets its own book and results.
RULES_VERSION = "pathA-v1.1"
# pathA-v1.1 keeps pathA-v1's strategy rules unchanged; it changes the simulator:
# spot prices for fills/marks/buy & hold, and decision times taken after the data
# arrived and the decision was computed. pathA-v1 stays frozen for reproduction.


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _order(decided_on: str, weight: float, data_ready_at: datetime, decided_at: datetime,
           notes: list[str], info: dict | None = None) -> dict:
    ready, done = _ts(data_ready_at), _ts(decided_at)
    if done < ready:
        raise ValueError("decided_at is earlier than data_ready_at")
    return {"decided_on": decided_on, "weight": weight, "data_ready_at": ready.isoformat(),
            "decided_at": done.isoformat(), "fill_at": (done.floor("1h") + pd.Timedelta(hours=1)).isoformat(),
            "notes": notes, **(info or {})}


def _entry(kind: str, o: dict) -> dict:
    return {"date": o["decided_on"], "kind": kind, "weight": o["weight"], "decided_on": o["decided_on"],
            "data_ready_at": o["data_ready_at"], "decided_at": o["decided_at"], "at": o["decided_at"],
            "fill_at": o["fill_at"], "notes": o["notes"] + [f"สั่งเติมที่ {o['fill_at']}"],
            **{k: v for k, v in o.items() if k.startswith("funding_") or k.startswith("inputs")}}


def new_state(decided_on: str, weight: float, data_ready_at: datetime, decided_at: datetime,
              notes: list[str], equity: float = START_EQUITY, info: dict | None = None) -> dict:
    """Open a book from cash on the close of ``decided_on``."""
    o = _order(decided_on, weight, data_ready_at, decided_at, notes, info)
    return {
        "version": VERSION,
        "rules_version": RULES_VERSION,
        "start_equity": equity,
        "last_day": decided_on,          # last closed day marked to market (or seen at opening)
        "weight": 0.0,                   # weight currently held
        "pending": o,                    # decided, not yet filled
        "book": {"cash": equity, "btc": 0.0},
        "buy_and_hold": {"cash": equity, "btc": 0.0},
        "history": [],
        "trades": [],
        "journal": [_entry("open", o)],
    }


def _rebalance(acct: dict, weight: float, o: float, costs: RegimeCosts) -> dict | None:
    eq_open = acct["cash"] + acct["btc"] * o
    delta = weight * eq_open - acct["btc"] * o
    if abs(delta) <= 1e-9 * eq_open:
        return None
    if delta > 0:
        px = o * (1 + costs.slip_bps / 1e4)
        spend = min(delta, acct["cash"])
        qty = spend * (1 - costs.fee) / px
        acct["cash"] -= spend
        acct["btc"] += qty
        return {"side": "BUY", "price": px, "qty": qty, "value": spend}
    px = o * (1 - costs.slip_bps / 1e4)
    qty = min(-delta / o, acct["btc"])
    value = qty * px * (1 - costs.fee)
    acct["cash"] += value
    acct["btc"] -= qty
    return {"side": "SELL", "price": px, "qty": qty, "value": value}


def advance(state: dict, daily: pd.DataFrame, hourly: pd.DataFrame,
            costs: RegimeCosts = RegimeCosts()) -> tuple[list[dict], list[str]]:
    """Fill the pending order once its 1h bar is in the data, and mark every
    newly closed day. Returns (journal entries added, problems that stopped it)."""
    added, problems = [], []
    day = _ts(state["last_day"]) + pd.Timedelta(days=1)
    while day in daily.index:
        row = daily.loc[day]
        c = float(row["close"])
        p = state.get("pending")
        fill_at = _ts(p["fill_at"]) if p else None
        if p and fill_at < day + pd.Timedelta(days=1):
            if fill_at <= _ts(p["decided_at"]):
                problems.append(f"simulator error: fill {fill_at.isoformat()} ไม่หลังเวลาตัดสินใจ {p['decided_at']}")
                break
            if fill_at not in hourly.index or not math.isfinite(float(hourly.loc[fill_at, "open"])):
                problems.append(f"ไม่มีราคาแท่ง 1 ชม. เวลา {fill_at.isoformat()} ยังเติมคำสั่งไม่ได้")
                break
            o = float(hourly.loc[fill_at, "open"])
            t = _rebalance(state["book"], p["weight"], o, costs)
            if t:
                state["trades"].append({"date": str(day.date()), "at": fill_at.isoformat(), "book": "paper",
                                        "decided_on": p["decided_on"], "decided_at": p["decided_at"], **t})
                added.append({"date": str(day.date()), "kind": "trade", "weight": p["weight"],
                              "at": fill_at.isoformat(), "decided_on": p["decided_on"],
                              "decided_at": p["decided_at"],
                              "notes": [f"{t['side']} {t['qty']:.6f} BTC @ {t['price']:,.2f}"
                                        f" (ตัดสินใจจากราคาปิด {p['decided_on']})"]})
            if not any(x["book"] == "buy_and_hold" for x in state["trades"]):
                tb = _rebalance(state["buy_and_hold"], 1.0, o, costs)
                if tb:
                    state["trades"].append({"date": str(day.date()), "at": fill_at.isoformat(),
                                            "book": "buy_and_hold", **tb})
            state["weight"] = p["weight"]
            state["pending"] = None
        if not math.isfinite(c):
            problems.append(f"ไม่มีราคาปิดของวันที่ {day.date()}")
            break
        eq = state["book"]["cash"] + state["book"]["btc"] * c
        bh = state["buy_and_hold"]["cash"] + state["buy_and_hold"]["btc"] * c
        state["history"].append({"date": str(day.date()), "close": c, "weight_held_at_close": state["weight"],
                                 "equity": eq, "buy_and_hold": bh})
        state["last_day"] = str(day.date())
        day += pd.Timedelta(days=1)
    later = daily.index[daily.index > day]
    if len(later) and day not in daily.index:
        problems.append(f"ข้อมูลวันที่ {day.date()} หายไป แต่มีวันหลังจากนั้น พอร์ตหยุดรอจนกว่าข้อมูลจะครบ")
    state["journal"].extend(added)
    return added, problems


def decide(state: dict, decided_on: str, weight: float | None, data_ready_at: datetime,
           decided_at: datetime, notes: list[str], info: dict | None = None) -> dict | None:
    """Record today's decision. ``weight`` None means not enough data: keep what is held."""
    if decided_on != state["last_day"]:
        return None                                    # only decide on the close the book has reached
    if any(e.get("decided_on") == decided_on for e in state["journal"]) or \
            (state.get("pending") or {}).get("decided_on") == decided_on:
        return None                                    # already decided on this close
    if weight is None:
        e = {"date": decided_on, "kind": "no_decision", "weight": state["weight"], "decided_on": decided_on,
             "data_ready_at": _ts(data_ready_at).isoformat(), "decided_at": _ts(decided_at).isoformat(),
             "at": _ts(decided_at).isoformat(), "notes": notes, **(info or {})}
        state["journal"].append(e)
        return e
    current = (state.get("pending") or {}).get("weight", state["weight"])
    if weight == current:
        return None
    o = _order(decided_on, weight, data_ready_at, decided_at, notes, info)
    state["pending"] = o
    e = _entry("decision", o)
    state["journal"].append(e)
    return e


def performance(state: dict) -> dict:
    h = state["history"]
    s0 = state["start_equity"]
    p = state.get("pending")
    base = {"weight_held": state["weight"], "pending_weight": p["weight"] if p else None,
            "pending_fill_at": p["fill_at"] if p else None,
            "trades": sum(1 for t in state["trades"] if t["book"] == "paper")}
    if not h:
        return {"days": 0, "equity": s0, "buy_and_hold": s0, "return": 0.0, "bh_return": 0.0,
                "max_dd": 0.0, "bh_max_dd": 0.0, **base}
    eq = pd.Series([s0] + [r["equity"] for r in h])
    bh = pd.Series([s0] + [r["buy_and_hold"] for r in h])
    dd = lambda x: float((1 - x / x.cummax()).max())
    return {"days": len(h), "since": h[0]["date"], "through": h[-1]["date"],
            "equity": float(eq.iloc[-1]), "buy_and_hold": float(bh.iloc[-1]),
            "return": float(eq.iloc[-1] / s0 - 1), "bh_return": float(bh.iloc[-1] / s0 - 1),
            "max_dd": dd(eq), "bh_max_dd": dd(bh), **base}


def render_th(p: dict, added: list[dict], problems: list[str]) -> str:
    lines = []
    if p["days"]:
        lines += [
            f"พอร์ตจำลอง {p['since']} ถึง {p['through']} ({p['days']} วัน)",
            f"- โปรแกรม: {p['equity']:,.0f} USDT ({p['return']:+.1%}), ลงหนักสุด {p['max_dd']:.1%}, ซื้อขาย {p['trades']} ครั้ง",
            f"- ซื้อ BTC ถือเฉยๆ: {p['buy_and_hold']:,.0f} USDT ({p['bh_return']:+.1%}), ลงหนักสุด {p['bh_max_dd']:.1%}",
        ]
    else:
        lines.append("พอร์ตจำลองยังไม่มีวันที่ปิดหลังซื้อขาย")
    lines.append(f"- ถือ BTC อยู่ {p['weight_held']:.0%}")
    if p["pending_weight"] is not None:
        lines.append(f"- รอเติมคำสั่ง {p['pending_weight']:.0%} ที่ {p['pending_fill_at']} (UTC)")
    for e in added:
        if e["kind"] == "decision":
            lines.append(f"- ตัดสินใจจากราคาปิด {e['decided_on']}: เปลี่ยนเป็น {e['weight']:.0%} ({', '.join(e['notes'])})")
        elif e["kind"] == "trade":
            lines.append(f"- {e['at']}: {e['notes'][0]}")
        elif e["kind"] == "no_decision":
            lines.append(f"- ไม่ตัดสินใจใหม่จากราคาปิด {e['decided_on']}: {', '.join(e['notes'])}")
    if p.get("funding_source"):
        lines.append(f"- ที่มา funding ({p.get('funding_policy')}): {p['funding_source']}; "
                     f"proxy {p.get('funding_proxy_days', 0)}/7 วัน")
    lines += [f"- ปัญหา: {x}" for x in problems]
    return "\n".join(lines)
