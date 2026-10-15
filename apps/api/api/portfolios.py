"""Read-only portfolio overview (BUILDPLAN v4.2 §22.3 Overview).

Reads ``trading.portfolios`` and each portfolio's latest ``trading.equity_snapshots``
row. Amounts stay decimal strings end to end; the browser never does money math.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from psycopg_pool import AsyncConnectionPool

STALE_AFTER = timedelta(hours=36)   # daily archive runs land about 8h after the UTC day closes

OVERVIEW_SQL = """
select p.label, p.mode, p.currency, p.initial_equity::text,
       s.at, s.trading_equity::text, s.economic_equity::text, s.high_water_mark::text, s.quality
from trading.portfolios p
left join lateral (
  select at, trading_equity, economic_equity, high_water_mark, quality
  from trading.equity_snapshots e where e.portfolio_id = p.id order by at desc limit 1
) s on true
order by p.label
"""


class PortfolioStore(Protocol):
    async def overview_rows(self) -> list[tuple[Any, ...]]: ...


class NoPortfolios:
    async def overview_rows(self) -> list[tuple[Any, ...]]:
        return []


class PgPortfolioStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def overview_rows(self) -> list[tuple[Any, ...]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(OVERVIEW_SQL)
            return list(await cur.fetchall())


def overview_items(rows: list[tuple[Any, ...]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    out = []
    for label, mode, currency, initial, at, trading, economic, hwm, quality in rows:
        out.append({
            "label": label, "mode": mode, "currency": currency, "initial_equity": initial,
            "as_of": at.isoformat() if at else None, "trading_equity": trading, "economic_equity": economic,
            "high_water_mark": hwm, "quality": quality,
            "stale": at is None or now - at > STALE_AFTER,
        })
    return out
