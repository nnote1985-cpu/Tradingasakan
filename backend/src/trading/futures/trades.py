"""Group fills into round-trip trades ("ไม้"): flat to open to flat again (futures-desk-plan.md §6).

One-way mode only. A fill that flips the position (long to short in one order) closes the old trade and
opens a new one with the remainder. Net P&L = realized P&L - commission (USDT) + funding paid or
received while the trade was open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class Trade:
    symbol: str
    side: str                     # LONG / SHORT
    opened_at: datetime
    qty: Decimal = Decimal(0)     # largest size held
    entry_cost: Decimal = Decimal(0)
    entry_qty: Decimal = Decimal(0)
    exit_value: Decimal = Decimal(0)
    exit_qty: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    closed_at: datetime | None = None
    fills: int = 0
    order_ids: set[int] = field(default_factory=set)

    def view(self) -> dict[str, Any]:
        net = self.realized - self.fees + self.funding
        return {
            "symbol": self.symbol, "side": self.side, "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None, "open": self.closed_at is None,
            "qty": self.qty, "entry_price": self.entry_cost / self.entry_qty if self.entry_qty else None,
            "exit_price": self.exit_value / self.exit_qty if self.exit_qty else None, "exit_qty": self.exit_qty,
            "realized_pnl": self.realized, "fees": self.fees, "funding": self.funding, "net_pnl": net,
            "fills": self.fills, "order_ids": sorted(self.order_ids),
        }


def round_trips(fills: list[dict[str, Any]], income: list[dict[str, Any]]) -> list[Trade]:
    """``fills`` and ``income`` rows as stored (fut_fills, fut_income). Oldest first in, oldest first out."""
    trades: list[Trade] = []
    pos: dict[str, Decimal] = {}
    cur: dict[str, Trade] = {}
    for f in sorted(fills, key=lambda x: (x["traded_at"], x["trade_id"])):
        sym, qty, price = f["symbol"], Decimal(f["qty"]), Decimal(f["price"])
        signed = qty if f["side"] == "BUY" else -qty
        fee = Decimal(f["commission"]) if f.get("commission_asset", "USDT") == "USDT" else Decimal(0)
        before = pos.get(sym, Decimal(0))
        after = before + signed
        t = cur.get(sym)
        if t is None or before == 0:
            # The first fill seen for a symbol may close a position opened before the lookback; it still starts a trade.
            t = Trade(sym, "LONG" if signed > 0 else "SHORT", f["traded_at"])
            cur[sym] = t
            trades.append(t)
        t.fills += 1
        t.order_ids.add(int(f["order_id"]))
        t.realized += Decimal(f["realized_pnl"])
        increasing = before == 0 or (before > 0) == (signed > 0)
        if increasing:
            t.entry_cost += price * qty
            t.entry_qty += qty
            t.fees += fee
            t.qty = max(t.qty, abs(after))
        else:
            closing = min(qty, abs(before))
            t.exit_value += price * closing
            t.exit_qty += closing
            t.fees += fee * closing / qty
            if after == 0 or (after > 0) != (before > 0):
                t.closed_at = f["traded_at"]
                del cur[sym]
                rest = qty - closing
                if rest > 0:     # flipped: the remainder opens the next trade
                    n = Trade(sym, "LONG" if after > 0 else "SHORT", f["traded_at"], qty=rest, entry_cost=price * rest,
                              entry_qty=rest, fees=fee * rest / qty, fills=1, order_ids={int(f["order_id"])})
                    cur[sym] = n
                    trades.append(n)
        pos[sym] = after
    for i in income:
        if i["kind"] != "FUNDING_FEE" or i.get("asset", "USDT") != "USDT":
            continue
        for t in trades:
            if t.symbol == i["symbol"] and t.opened_at <= i["at"] and (t.closed_at is None or i["at"] <= t.closed_at):
                t.funding += Decimal(i["amount"])
                break
    return trades
