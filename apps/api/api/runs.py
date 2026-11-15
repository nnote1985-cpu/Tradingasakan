"""Backtest and Shadow runs on the chart (docs/m2-charts-plan.md §4.4, E4).

A run is drawn on the same chart as live bars, with its own mode label and never mixed with
another run: ``BACKTEST`` = a committed result under research/results/ (simulator fills); ``SHADOW``
= a paper book in ``trading.portfolios`` (daily snapshots; its trades are the days its BTC
exposure changed, read from the snapshots, since paper books keep fills in their own journals).

Each run gives the venue and date range of the bars to draw, ``events`` for markers and the equity
and drawdown series for the lower panel. Amounts leave as decimal strings.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from api.portfolios import equity_series

RATIO = Decimal("0.000001")


def _t(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day[:10]), datetime.min.time(), UTC).timestamp())


def _venue(market: str | None, label: str = "") -> str:
    return "perp" if market == "futures" or label.startswith("futures-") else "spot"


def run_list(backtests: list[dict[str, Any]], portfolios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [{"kind": "backtest", "id": b["id"], "label": f"{b['id']} · {b['name']}", "mode": "BACKTEST",
            "venue": _venue(b.get("market")), "range": b["range"]} for b in backtests]
    out += [{"kind": "shadow", "id": p["label"], "label": p["label"], "mode": "SHADOW", "venue": _venue(None, p["label"]), "range": None}
            for p in portfolios if p.get("mode") == "SHADOW" and not p["label"].endswith("/buy_and_hold") and p.get("as_of")]
    return out


def backtest_run(b: dict[str, Any]) -> dict[str, Any]:
    """A result from ``load_backtests`` (points with drawdowns, fills paired into trades)."""
    events = []
    for tr in b.get("trades", []):
        long = tr["side"] == "long"
        events.append({"t": _t(tr["entry_day"]), "side": "BUY" if long else "SELL", "action": "open", "price": tr["entry_price"],
                       "qty": tr["qty"], "text": f"เปิด {'long' if long else 'short'}"})
        if tr.get("exit_day"):
            events.append({"t": _t(tr["exit_day"]), "side": "SELL" if long else "BUY", "action": "close", "price": tr["exit_price"],
                           "qty": tr["qty"], "text": f"ปิด {tr['pnl']}"})
    pts = b["points"]
    return {"kind": "backtest", "id": b["id"], "label": b["name"], "mode": "BACKTEST", "venue": _venue(b.get("market")),
            "range": b["range"], "note": b.get("note"), "events": sorted(events, key=lambda e: e["t"]),
            "equity": [{"t": _t(p["day"]), "v": p["strategy"]} for p in pts],
            "benchmark": [{"t": _t(p["day"]), "v": p["buy_and_hold"]} for p in pts],
            "drawdown": [{"t": _t(p["day"]), "v": p["strategy_drawdown"]} for p in pts]}


def shadow_run(portfolio: tuple[Any, ...], rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Equity, drawdown and the days the book's BTC exposure share changed (≥ 1 percentage point)."""
    s = equity_series(portfolio, rows)
    pts = s["points"]
    events = []
    prev: Decimal | None = None
    for p in pts:
        if p["exposure"] is None or p["mark_price"] is None:
            continue
        eq = Decimal(p["economic_equity"])
        share = (Decimal(p["exposure"]) / eq).quantize(Decimal("0.01")) if eq > 0 else Decimal(0)
        if prev is not None and abs(share - prev) >= Decimal("0.01"):
            events.append({"t": _t(p["at"]), "side": "BUY" if share > prev else "SELL", "action": "rebalance", "price": p["mark_price"],
                           "qty": None, "text": f"ถือ {prev:.0%} → {share:.0%}"})
        prev = share
    first = pts[0]["at"][:10] if pts else None
    last = pts[-1]["at"][:10] if pts else None
    return {"kind": "shadow", "id": s["label"], "label": s["label"], "mode": s["mode"], "venue": _venue(None, s["label"]),
            "range": [first, last] if pts else None, "note": "จุดซื้อขายอ่านจากวันที่สัดส่วน BTC ใน snapshot รายวันเปลี่ยน",
            "events": events, "equity": [{"t": _t(p["at"]), "v": p["economic_equity"]} for p in pts], "benchmark": [],
            "drawdown": [{"t": _t(p["at"]), "v": p["drawdown"]} for p in pts]}
