"""Forward paper trading for path A: simulated money only, no exchange orders.

The book follows ``suggested_btc_weight`` from the daily risk check. A weight is
frozen in the ledger at the close it was decided on and traded at the next
day's open, so later data revisions (estimated funding replaced by actual
prints) never rewrite past decisions. A BTC buy & hold book with the same
starting cash and costs runs alongside as the benchmark.
"""

from __future__ import annotations

import math

import pandas as pd

from trading.strategies.trend_regime_v2 import RegimeCosts

START_EQUITY = 10_000.0


def new_state(decided_on: str, weight: float, reason: list[str], equity: float = START_EQUITY) -> dict:
    """Open a book on the close of ``decided_on``; the first trade is the next day's open."""
    return {
        "start_equity": equity,
        "last_day": decided_on,
        "target_weight": weight,
        "book": {"cash": equity, "btc": 0.0},
        "buy_and_hold": {"cash": equity, "btc": 0.0},
        "history": [],
        "trades": [],
        "journal": [{"date": decided_on, "kind": "open",
                     "weight": weight, "notes": reason}],
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


def advance(state: dict, daily: pd.DataFrame, sig: pd.DataFrame, events_by_day: dict | None = None,
            costs: RegimeCosts = RegimeCosts()) -> list[dict]:
    """Process every closed day after ``state['last_day']``. Mutates ``state``;
    returns the journal entries added (weight changes and trades)."""
    added = []
    last = pd.Timestamp(state["last_day"], tz="UTC")
    for d, row in daily.loc[daily.index > last].iterrows():
        o, c = float(row["open"]), float(row["close"])
        if not (math.isfinite(o) and math.isfinite(c)):
            break                                  # never skip a day; wait for complete data
        day = str(d.date())
        w = state["target_weight"]
        t = _rebalance(state["book"], w, o, costs)
        if t:
            state["trades"].append({"date": day, "book": "paper", **t})
        if not state["history"]:                   # benchmark buys once, on the first day
            tb = _rebalance(state["buy_and_hold"], 1.0, o, costs)
            if tb:
                state["trades"].append({"date": day, "book": "buy_and_hold", **tb})
        eq = state["book"]["cash"] + state["book"]["btc"] * c
        bh = state["buy_and_hold"]["cash"] + state["buy_and_hold"]["btc"] * c
        state["history"].append({"date": day, "close": c, "weight_held": w, "equity": eq, "buy_and_hold": bh})
        if t:
            added.append({"date": day, "kind": "trade", "weight": w,
                          "notes": [f"{t['side']} {t['qty']:.6f} BTC @ {t['price']:,.2f}"]})
        new_w = float(sig["suggested_btc_weight"].get(d, float("nan")))
        if math.isfinite(new_w) and new_w != w:
            state["target_weight"] = new_w
            added.append({"date": day, "kind": "decision", "weight": new_w,
                          "notes": list((events_by_day or {}).get(day, []))})
        state["last_day"] = day
    state["journal"].extend(added)
    return added


def performance(state: dict) -> dict:
    h = state["history"]
    s0 = state["start_equity"]
    if not h:
        return {"days": 0, "equity": s0, "buy_and_hold": s0, "return": 0.0, "bh_return": 0.0,
                "max_dd": 0.0, "bh_max_dd": 0.0}
    eq = pd.Series([s0] + [r["equity"] for r in h])
    bh = pd.Series([s0] + [r["buy_and_hold"] for r in h])
    dd = lambda x: float((1 - x / x.cummax()).max())
    return {"days": len(h), "since": h[0]["date"], "through": h[-1]["date"],
            "equity": float(eq.iloc[-1]), "buy_and_hold": float(bh.iloc[-1]),
            "return": float(eq.iloc[-1] / s0 - 1), "bh_return": float(bh.iloc[-1] / s0 - 1),
            "max_dd": dd(eq), "bh_max_dd": dd(bh), "weight_now": state["target_weight"],
            "trades": sum(1 for t in state["trades"] if t["book"] == "paper")}


def render_th(p: dict, added: list[dict]) -> str:
    if not p["days"]:
        return "พอร์ตจำลองเปิดแล้ว ยังไม่มีวันซื้อขาย (ซื้อครั้งแรกที่ราคาเปิดของวันถัดไป)"
    lines = [
        f"พอร์ตจำลอง {p['since']} ถึง {p['through']} ({p['days']} วัน)",
        f"- โปรแกรม: {p['equity']:,.0f} USDT ({p['return']:+.1%}), ลงหนักสุด {p['max_dd']:.1%}, ซื้อขาย {p['trades']} ครั้ง",
        f"- ซื้อ BTC ถือเฉยๆ: {p['buy_and_hold']:,.0f} USDT ({p['bh_return']:+.1%}), ลงหนักสุด {p['bh_max_dd']:.1%}",
        f"- สัดส่วน BTC ที่จะถือพรุ่งนี้: {p['weight_now']:.0%}",
    ]
    for e in added:
        if e["kind"] == "decision":
            why = ", ".join(e["notes"]) or "สัญญาณเปลี่ยน"
            lines.append(f"- {e['date']}: เปลี่ยนเป้าเป็น {e['weight']:.0%} ({why})")
        elif e["kind"] == "trade":
            lines.append(f"- {e['date']}: {e['notes'][0]}")
    return "\n".join(lines)
