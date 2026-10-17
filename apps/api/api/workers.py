"""Read-only Testnet worker status for the web Worker tab (testnet-development-plan.md §9, §9.1 GET /api/v1/workers).

Reads what the worker writes since P3: ``trading.worker_heartbeats``, the open
account epoch, the latest reconcile run and the reconciled balances. Nothing
here sends a command to the worker; pause/resume buttons come with P5's
audited command table. Before the P3 migration is applied the tables do not
exist and the endpoint answers ``"available": false``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool

SILENT_AFTER = timedelta(seconds=60)          # the worker writes about every 10 s
SHOWN_ASSETS = ("BTC", "USDT", "BNB", "ETH")  # the Testnet account holds hundreds of assets; the page shows these

WORKERS_SQL = """
select h.account, h.owner, h.host, h.started_at, h.last_tick_at, h.body, h.updated_at,
       e.epoch, e.opened_at,
       r.verdict, r.checked_at, r.differences, r.open_orders,
       (select count(*) from trading.exec_balances b where b.account = h.account and b.epoch = e.epoch),
       (select coalesce(json_agg(json_build_object('asset', b.asset, 'free', trim_scale(b.free)::text, 'locked', trim_scale(b.locked)::text)
                                 order by b.asset), '[]'::json)
          from trading.exec_balances b where b.account = h.account and b.epoch = e.epoch and b.asset = any(%s)),
       (select count(*) from trading.exec_trades t where t.account = h.account and t.epoch = e.epoch)
from trading.worker_heartbeats h
left join trading.exec_account_epochs e on e.account = h.account and e.status = 'OPEN'
left join lateral (
  select verdict, checked_at, differences, open_orders from trading.exec_reconciliations x
  where x.account = h.account order by checked_at desc, id desc limit 1
) r on true
order by h.account
"""


class Unavailable(Exception):
    """The P3 tables have not been created yet."""


class WorkerStore(Protocol):
    async def worker_rows(self) -> list[tuple[Any, ...]]: ...


class NoWorkers:
    async def worker_rows(self) -> list[tuple[Any, ...]]:
        raise Unavailable


class PgWorkerStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def worker_rows(self) -> list[tuple[Any, ...]]:
        try:
            async with self.pool.connection() as conn:
                cur = await conn.execute(WORKERS_SQL, (list(SHOWN_ASSETS),))
                return list(await cur.fetchall())
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def worker_items(rows: list[tuple[Any, ...]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    out = []
    for (account, owner, host, started, last_tick, body, updated, epoch, opened, verdict, checked, diffs, n_orders,
         n_assets, balances, n_trades) in rows:
        body = body or {}
        feeds = body.get("feeds") or {}
        out.append({
            "account": account, "owner": owner, "host": host, "started_at": _iso(started), "last_tick_at": _iso(last_tick),
            "updated_at": _iso(updated), "silent": updated is None or now - updated > SILENT_AFTER,
            "mode": body.get("mode"), "execution_enabled": body.get("execution_enabled"), "safety": body.get("safety"),
            "lease_token": body.get("lease_token"),
            "clock": {"offset_ms": body.get("clock_offset_ms"), "status": body.get("clock_status"),
                      "signing_paused": body.get("signing_paused")},
            "account_ok": body.get("account_ok"), "deadman_ok": body.get("deadman_ok"),
            "streams": feeds.get("streams") or {}, "quote_age_ms": feeds.get("quote_age_ms"),
            "bars": {"last_1m": feeds.get("bars_1m_last_open"), "last_1d": feeds.get("bars_1d_last_open"),
                     "gaps": feeds.get("bar_gaps"), "revisions": feeds.get("bar_revisions")},
            "persist": body.get("persist"), "execution": body.get("execution"),
            "epoch": None if epoch is None else {"epoch": epoch, "opened_at": _iso(opened)},
            "reconcile": None if verdict is None else {"verdict": verdict, "checked_at": _iso(checked),
                                                       "differences": diffs or [], "open_orders": n_orders},
            "balances": balances or [], "asset_count": n_assets or 0, "trades": n_trades or 0,
        })
    return out
