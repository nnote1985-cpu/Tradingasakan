"""Backtest results for the web (§21.3): files under research/results/, committed with the
research they come from. They are history, not portfolios, and are labelled as such.

Drawdowns are computed here with Decimal, like the portfolio endpoints; the browser only formats.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parents[3] / "research" / "results"
RATIO = Decimal("0.000001")


def _drawdowns(values: list[Decimal]) -> list[Decimal]:
    peak, out = Decimal(0), []
    for v in values:
        peak = max(peak, v)
        out.append(((peak - v) / peak).quantize(RATIO) if peak > 0 else Decimal(0))
    return out


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
        items.append(r)
    return items
