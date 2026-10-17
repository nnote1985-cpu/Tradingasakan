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


ORDERS_SQL = """
select i.client_order_id, i.book, i.leg, i.side, i.order_type, trim_scale(i.qty)::text, trim_scale(i.limit_price)::text, i.created_at,
       e.state, trim_scale(e.cum_qty)::text, e.at, e.reason,
       (select trim_scale(round(sum(f.qty * f.price) / nullif(sum(f.qty), 0), 2))::text from trading.exec_fills f
         where f.client_order_id = i.client_order_id)
from trading.exec_intents i
join lateral (select state, cum_qty, at, reason from trading.exec_order_events x where x.client_order_id = i.client_order_id
              order by seq desc limit 1) e on true
where i.account = %s
order by i.created_at desc, i.client_order_id
limit %s
"""

TRADES_SQL = """
select t.traded_at, t.epoch, t.trade_id, t.side, trim_scale(t.qty)::text, trim_scale(t.price)::text,
       trim_scale(round(t.qty * t.price, 8))::text, trim_scale(t.commission)::text, t.commission_asset, t.client_order_id,
       i.book, i.leg
from trading.exec_trades t
left join trading.exec_intents i on i.client_order_id = t.client_order_id
where t.account = %s
order by t.traded_at desc, t.trade_id desc
limit %s
"""
ACTIVITY_LIMIT = 50


class Unavailable(Exception):
    """The P3 tables have not been created yet."""


class WorkerStore(Protocol):
    async def worker_rows(self) -> list[tuple[Any, ...]]: ...
    async def activity_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]: ...


class NoWorkers:
    async def worker_rows(self) -> list[tuple[Any, ...]]:
        raise Unavailable

    async def activity_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
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


    async def activity_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        try:
            async with self.pool.connection() as conn:
                orders = list(await (await conn.execute(ORDERS_SQL, (account, limit))).fetchall())
                trades = list(await (await conn.execute(TRADES_SQL, (account, limit))).fetchall())
                return orders, trades
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


def activity_items(orders: list[tuple[Any, ...]], trades: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Latest Testnet orders (with their last state) and trades for one account; numbers stay as exact decimal text."""
    return {
        "orders": [{"client_order_id": cid, "book": book, "leg": leg, "side": side, "type": otype, "qty": qty, "limit_price": price,
                    "created_at": _iso(created), "state": state, "filled_qty": cum, "updated_at": _iso(at), "reason": reason,
                    "avg_price": avg}
                   for cid, book, leg, side, otype, qty, price, created, state, cum, at, reason, avg in orders],
        "trades": [{"traded_at": _iso(at), "epoch": epoch, "trade_id": tid, "side": side, "qty": qty, "price": price, "quote_qty": quote,
                    "commission": fee, "commission_asset": asset, "client_order_id": cid or None, "book": book, "leg": leg}
                   for at, epoch, tid, side, qty, price, quote, fee, asset, cid, book, leg in trades],
    }
