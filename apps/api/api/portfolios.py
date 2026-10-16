"""Read-only portfolio overview and equity history (BUILDPLAN v4.2 §22.1, §22.3).

Reads ``trading.portfolios`` and ``trading.equity_snapshots``. Amounts stay decimal
strings end to end; returns and drawdowns are computed here in Decimal, so the browser
only formats numbers and never does money math.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from psycopg_pool import AsyncConnectionPool

STALE_AFTER = timedelta(hours=36)   # daily archive runs land about 8h after the UTC day closes
RATIO = Decimal("0.000001")         # returns and drawdowns are sent as fractions with 6 places

OVERVIEW_SQL = """
select p.label, p.mode, p.currency, p.initial_equity::text,
       s.at, s.trading_equity::text, s.economic_equity::text, s.high_water_mark::text, s.quality,
       greatest(p.initial_equity, m.peak)::text, s.exposure::text
from trading.portfolios p
left join lateral (
  select at, trading_equity, economic_equity, high_water_mark, quality, exposure
  from trading.equity_snapshots e where e.portfolio_id = p.id order by at desc limit 1
) s on true
left join lateral (
  select max(economic_equity) as peak from trading.equity_snapshots e where e.portfolio_id = p.id
) m on true
order by p.label
"""

PORTFOLIO_SQL = "select label, mode, currency, initial_equity::text from trading.portfolios where label = %s"

EQUITY_SQL = """
select e.at, e.trading_equity::text, e.economic_equity::text, e.mark_price::text, e.exposure::text, e.quality
from trading.equity_snapshots e join trading.portfolios p on p.id = e.portfolio_id
where p.label = %s order by e.at
"""


class PortfolioStore(Protocol):
    async def overview_rows(self) -> list[tuple[Any, ...]]: ...
    async def portfolio(self, label: str) -> tuple[Any, ...] | None: ...
    async def equity_rows(self, label: str) -> list[tuple[Any, ...]]: ...


class NoPortfolios:
    async def overview_rows(self) -> list[tuple[Any, ...]]:
        return []

    async def portfolio(self, label: str) -> tuple[Any, ...] | None:
        return None

    async def equity_rows(self, label: str) -> list[tuple[Any, ...]]:
        return []


class PgPortfolioStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def overview_rows(self) -> list[tuple[Any, ...]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(OVERVIEW_SQL)
            return list(await cur.fetchall())

    async def portfolio(self, label: str) -> tuple[Any, ...] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(PORTFOLIO_SQL, (label,))
            return await cur.fetchone()

    async def equity_rows(self, label: str) -> list[tuple[Any, ...]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(EQUITY_SQL, (label,))
            return list(await cur.fetchall())


def _ratio(num: Decimal, den: Decimal) -> str:
    return str((num / den - 1).quantize(RATIO))


def _drawdown(equity: Decimal, peak: Decimal) -> str:
    return str((1 - equity / peak).quantize(RATIO)) if peak > 0 else "0.000000"


def overview_items(rows: list[tuple[Any, ...]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    out = []
    for label, mode, currency, initial, at, trading, economic, hwm, quality, peak, exposure in rows:
        econ = Decimal(economic) if economic is not None else None
        out.append({
            "label": label, "mode": mode, "currency": currency, "initial_equity": initial,
            "as_of": at.isoformat() if at else None, "trading_equity": trading, "economic_equity": economic,
            "high_water_mark": hwm, "quality": quality,
            "stale": at is None or now - at > STALE_AFTER,
            "return": None if econ is None else _ratio(econ, Decimal(initial)),
            "drawdown": None if econ is None or peak is None else _drawdown(econ, Decimal(peak)),
            "exposure_share": None if econ is None or exposure is None or econ == 0 else str((Decimal(exposure) / econ).quantize(RATIO)),
        })
    return out


def equity_series(portfolio: tuple[Any, ...], rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Every snapshot with its return and its drawdown from the running peak of economic equity
    (the initial equity counts as the first peak)."""
    label, mode, currency, initial = portfolio
    start = Decimal(initial)
    peak, max_dd = start, Decimal(0)
    points = []
    for at, trading, economic, mark, exposure, quality in rows:
        econ = Decimal(economic)
        peak = max(peak, econ)
        dd = Decimal(_drawdown(econ, peak))
        max_dd = max(max_dd, dd)
        points.append({"at": at.isoformat(), "trading_equity": trading, "economic_equity": economic,
                       "mark_price": mark, "exposure": exposure, "quality": quality,
                       "return": _ratio(econ, start), "drawdown": str(dd)})
    return {"label": label, "mode": mode, "currency": currency, "initial_equity": initial,
            "points": points, "max_drawdown": str(max_dd.quantize(RATIO))}
