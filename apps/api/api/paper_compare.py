"""Side-by-side view of the running paper books (3a = pathA-r10a-hedge against R10a and the rest).

Read-only. Reads ``trading.equity_snapshots``, ``trading.journal_lines`` (fees and funding totals)
and the fill entries of ``trading.journal_entries``, all written by the daily sync
(scripts/trading_sync_sql.py). Everything is computed here in Decimal; the browser only formats.

Books start on different days, so every book is measured from a common start day ``from``:
a book already running on ``from`` is measured from its equity at that day's close, a book
opened later is measured from its starting equity. Drawdown is from the running peak inside
that window. Positions come from the fill memos (``BUY|SELL <qty> BTC @ <price>``) the sync
writes; a key with ``:perp:`` (3a's perp wallet) or a ``futures-`` book is the perp leg.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from psycopg_pool import AsyncConnectionPool

STALE_AFTER = timedelta(hours=36)
RATIO = Decimal("0.000001")
MONEY = Decimal("0.01")
QTY = Decimal("0.000001")
FOCUS = "pathA-r10a-hedge"          # 3a, the book this page is built around
FILL = re.compile(r"^(BUY|SELL) ([0-9]+(?:\.[0-9]+)?) BTC @ ")

SNAPSHOT_SQL = """
select p.label, p.initial_equity::text, e.at, e.economic_equity::text, e.mark_price::text
from trading.portfolios p join trading.equity_snapshots e on e.portfolio_id = p.id
order by p.label, e.at
"""

TOTALS_SQL = """
select p.label, l.ledger_account, sum(l.amount)::text
from trading.journal_lines l
join trading.journal_entries j on j.id = l.entry_id
join trading.portfolios p on p.id = j.portfolio_id
where l.ledger_account in ('fees', 'funding')
group by p.label, l.ledger_account
"""

FILLS_SQL = r"""
select p.label, j.source_event_key, j.memo
from trading.journal_entries j join trading.portfolios p on p.id = j.portfolio_id
where j.memo ~ '^(BUY|SELL) '
order by j.occurred_at, j.source_event_key
"""


class CompareStore(Protocol):
    async def snapshot_rows(self) -> list[tuple[Any, ...]]: ...
    async def total_rows(self) -> list[tuple[Any, ...]]: ...
    async def fill_rows(self) -> list[tuple[Any, ...]]: ...


class NoCompare:
    async def snapshot_rows(self) -> list[tuple[Any, ...]]:
        return []

    async def total_rows(self) -> list[tuple[Any, ...]]:
        return []

    async def fill_rows(self) -> list[tuple[Any, ...]]:
        return []


class PgCompareStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def _rows(self, sql: str) -> list[tuple[Any, ...]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql)
            return list(await cur.fetchall())

    async def snapshot_rows(self) -> list[tuple[Any, ...]]:
        return await self._rows(SNAPSHOT_SQL)

    async def total_rows(self) -> list[tuple[Any, ...]]:
        return await self._rows(TOTALS_SQL)

    async def fill_rows(self) -> list[tuple[Any, ...]]:
        return await self._rows(FILLS_SQL)


def _r(x: Decimal) -> str:
    return str(x.quantize(RATIO))


def _leg(label: str, key: str) -> str:
    return "perp" if ":perp:" in key or label.startswith("futures-") else "spot"


def positions(fill_rows: list[tuple[Any, ...]]) -> dict[str, dict[str, Decimal]]:
    """Net BTC per leg for every portfolio, signed (short < 0)."""
    out: dict[str, dict[str, Decimal]] = {}
    for label, key, memo in fill_rows:
        m = FILL.match(memo or "")
        if not m:
            continue
        qty = Decimal(m.group(2)) * (1 if m.group(1) == "BUY" else -1)
        legs = out.setdefault(label, {"spot": Decimal(0), "perp": Decimal(0)})
        legs[_leg(label, key)] += qty
    return out


def _window(initial: Decimal, pts: list[tuple[date, Decimal]], start: date) -> tuple[Decimal, list[tuple[date, Decimal]]]:
    """(base, points) measured from ``start``: the close on or before it if the book was already
    running, otherwise the starting equity."""
    before = [p for p in pts if p[0] <= start]
    if not before or pts[0][0] >= start:
        return initial, pts
    base_pt = before[-1]
    return base_pt[1], [base_pt] + [p for p in pts if p[0] > start]


def compare_view(snapshots: list[tuple[Any, ...]], totals: list[tuple[Any, ...]], fills: list[tuple[Any, ...]],
                 start: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    series: dict[str, dict[str, Any]] = {}
    for label, initial, at, economic, mark in snapshots:
        s = series.setdefault(label, {"initial": Decimal(initial), "points": [], "at": None, "mark": None})
        day = at.astimezone(UTC).date()
        if s["points"] and s["points"][-1][0] == day:     # one close per day; keep the latest
            s["points"].pop()
        s["points"].append((day, Decimal(economic)))
        s["at"], s["mark"] = at, (Decimal(mark) if mark is not None else None)

    live = {lab: s for lab, s in series.items() if s["points"] and now - s["at"] <= STALE_AFTER}
    papers = sorted(lab for lab in live if lab.endswith("/paper"))
    holds = [lab for lab in live if lab.endswith("/buy_and_hold")]
    # One buy & hold line is enough once every book is measured from the same day: the longest spot
    # one (a perp buy & hold also pays funding, so it is only the fallback).
    bench = min(holds, key=lambda lab: (lab.startswith("futures-"), live[lab]["points"][0][0], lab)) if holds else None
    labels = papers + ([bench] if bench else [])

    opened = {lab.split("/")[0]: live[lab]["points"][0][0] for lab in papers}
    default_start = opened.get(FOCUS) or (min(opened.values()) if opened else None)
    earliest = min((live[lab]["points"][0][0] for lab in labels), default=None)
    start = start or default_start

    sums: dict[str, dict[str, Decimal]] = {}
    for label, account, amount in totals:
        sums.setdefault(label, {})[account] = Decimal(amount)
    legs = positions(fills)

    books = []
    for lab in labels:
        s = live[lab]
        initial, pts = s["initial"], s["points"]
        base, win = _window(initial, pts, start) if start else (initial, pts)
        peak, max_dd, out = base, Decimal(0), []
        for day, eq in win:
            peak = max(peak, eq)
            dd = 1 - eq / peak if peak > 0 else Decimal(0)
            max_dd = max(max_dd, dd)
            out.append({"day": day.isoformat(), "equity": str(eq.quantize(MONEY)), "return": _r(eq / base - 1), "drawdown": _r(dd)})
        last = pts[-1][1]
        leg = legs.get(lab, {"spot": Decimal(0), "perp": Decimal(0)})
        mark = s["mark"]

        def share(q: Decimal, mark: Decimal | None = mark, last: Decimal = last) -> str | None:
            if q == 0:
                return "0.000000"
            return None if mark is None or last == 0 else _r(q * mark / last)

        version, arm = lab.split("/", 1)
        kind = "hedge" if leg["spot"] != 0 and leg["perp"] != 0 or version == FOCUS else (
            "perp" if version.startswith("futures-") else "spot")
        fees = sums.get(lab, {}).get("fees", Decimal(0))
        funding = -sums.get(lab, {}).get("funding", Decimal(0))     # the ledger books funding paid as positive
        books.append({
            "label": lab, "version": version, "arm": arm, "kind": kind,
            "opened_on": pts[0][0].isoformat(), "as_of": s["at"].isoformat(), "days": len(pts),
            "initial_equity": str(initial), "equity": str(last.quantize(MONEY)),
            "return_since_open": _r(last / initial - 1),
            "return": out[-1]["return"] if out else None,
            "drawdown": out[-1]["drawdown"] if out else None,
            "max_drawdown": _r(max_dd),
            "fees": str(fees.quantize(MONEY)), "funding_received": str(funding.quantize(MONEY)),
            "mark_price": None if mark is None else str(mark.quantize(MONEY)),
            "spot_qty": str(leg["spot"].quantize(QTY)), "perp_qty": str(leg["perp"].quantize(QTY)),
            "spot_share": share(leg["spot"]), "perp_share": share(leg["perp"]),
            "benchmark": arm == "buy_and_hold",
            "points": out,
        })
    return {"from": start.isoformat() if start else None,
            "default_from": default_start.isoformat() if default_start else None,
            "earliest": earliest.isoformat() if earliest else None,
            "focus": FOCUS, "books": books}
