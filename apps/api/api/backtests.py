"""Backtest results for the web (§21.3): files under research/results/, committed with the
research they come from. They are history, not portfolios, and are labelled as such.

Drawdowns are computed here with Decimal, like the portfolio endpoints; the browser only formats.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parents[3] / "research" / "results"
RATIO = Decimal("0.000001")
CENT = Decimal("0.01")


def _drawdowns(values: list[Decimal]) -> list[Decimal]:
    peak, out = Decimal(0), []
    for v in values:
        peak = max(peak, v)
        out.append(((peak - v) / peak).quantize(RATIO) if peak > 0 else Decimal(0))
    return out


def round_trips(fills: list[dict[str, str]], initial: Decimal, last_day: str, last_equity: Decimal) -> list[dict[str, Any]]:
    """Pair open and close legs into trades. A trade's P&L is the equity after its close minus the
    equity before its open, so it includes both fees, slippage and the funding paid while held;
    the trades sum to the run's net. A trade still open at the end is marked to the last close."""
    trades: list[dict[str, Any]] = []
    before = initial
    cur: dict[str, Any] | None = None
    for f in fills:
        if f["action"] == "open":
            cur = {"n": len(trades) + 1, "side": f["side"], "entry_day": f["day"], "entry_price": f["price"],
                   "qty": f["qty"], "exit_day": None, "exit_price": None}
            continue
        if cur is None:
            raise ValueError(f"close without an open on {f['day']}")
        after = Decimal(f["equity"])
        trades.append(_close(cur, f["day"], f["price"], before, after))
        before, cur = after, None
    if cur:
        trades.append(_close(cur, last_day, None, before, last_equity))
    return trades


def _close(t: dict[str, Any], day: str, price: str | None, before: Decimal, after: Decimal) -> dict[str, Any]:
    pnl = (after - before).quantize(CENT)
    return {**t, "exit_day": day if price else None, "exit_price": price, "open": price is None,
            "days": (date.fromisoformat(day) - date.fromisoformat(t["entry_day"])).days,
            "pnl": str(pnl), "return": str((pnl / before).quantize(RATIO))}


def trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate counts closed trades only: a trade still open has no result yet."""
    pnl = {s: sum((Decimal(t["pnl"]) for t in trades if t["side"] == s), Decimal(0)) for s in ("long", "short")}
    closed = [t for t in trades if not t["open"]]
    closed_wins = sum(Decimal(t["pnl"]) > 0 for t in closed)
    closed_pnl = sum((Decimal(t["pnl"]) for t in closed), Decimal(0))
    open_pnl = sum((Decimal(t["pnl"]) for t in trades if t["open"]), Decimal(0))
    return {"count": len(trades), "wins": sum(Decimal(t["pnl"]) > 0 for t in trades),
            "long_pnl": str(pnl["long"]), "short_pnl": str(pnl["short"]),
            "closed": len(closed), "closed_wins": closed_wins,
            "win_rate": str((Decimal(closed_wins) / len(closed)).quantize(RATIO)) if closed else None,
            "closed_pnl": str(closed_pnl), "open_pnl": str(open_pnl), "net_pnl": str(closed_pnl + open_pnl)}


def load_backtests(market: str | None = None, results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    items = []
    for p in sorted(results_dir.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if market and r.get("market") != market:
            continue
        strat = [Decimal(x["strategy"]) for x in r["points"]]
        bh = [Decimal(x["buy_and_hold"]) for x in r["points"]]
        r["points"] = [{**x, "strategy_drawdown": str(ds), "buy_and_hold_drawdown": str(db)}
                       for x, ds, db in zip(r["points"], _drawdowns(strat), _drawdowns(bh), strict=True)]
        if "fills" in r:
            last = r["points"][-1]
            r["trades"] = round_trips(r.pop("fills"), Decimal(r["initial_equity"]), last["day"], Decimal(last["strategy"]))
            r["trade_stats"] = trade_stats(r["trades"])
        items.append(r)
    return items
