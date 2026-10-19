"""Read-only Testnet worker status for the web Worker tab (testnet-development-plan.md §9, §9.1 GET /api/v1/workers).

Reads what the worker writes since P3: ``trading.worker_heartbeats``, the open
account epoch, the latest reconcile run and the reconciled balances. Nothing
here talks to the worker directly: HALT and RESUME from the page are rows in
``trading.exec_operator_requests`` (P5), the same audited queue the CLI
``scripts/testnet_request.py`` writes, which Guardian carries out under the
writer lease. Before the P3 migration is applied the tables do not exist and
the endpoint answers ``"available": false``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
PNL_ASSETS = ("BTC", "USDT")        # the bot trades BTCUSDT only; other Testnet assets are not marked

BALANCES_SQL = """
select e.epoch, e.opened_at, a.asset,
       coalesce((e.opening_balances -> a.asset ->> 'free')::numeric, 0) + coalesce((e.opening_balances -> a.asset ->> 'locked')::numeric, 0),
       (select b.free + b.locked from trading.exec_balances b where b.account = e.account and b.epoch = e.epoch and b.asset = a.asset)
from trading.exec_account_epochs e cross join unnest(%s::text[]) as a(asset)
where e.account = %s and e.status = 'OPEN'
order by a.asset
"""

PNL_SQL = """
select coalesce(i.book, ''),
       count(*) filter (where t.side = 'BUY'), count(*) filter (where t.side = 'SELL'),
       sum(case when t.side = 'BUY' then t.qty else -t.qty end)
         - coalesce(sum(t.commission) filter (where t.commission_asset = 'BTC'), 0),
       sum(case when t.side = 'SELL' then t.qty * t.price else -t.qty * t.price end)
         - coalesce(sum(t.commission) filter (where t.commission_asset = 'USDT'), 0),
       coalesce(sum(t.commission) filter (where t.commission_asset = 'BNB'), 0),
       sum(t.qty * t.price),
       (array_agg(t.price order by t.traded_at desc, t.trade_id desc))[1]
from trading.exec_trades t
join trading.exec_account_epochs e on e.account = t.account and e.epoch = t.epoch and e.status = 'OPEN'
left join trading.exec_intents i on i.client_order_id = t.client_order_id
where t.account = %s and t.symbol = 'BTCUSDT'
group by 1
order by 1
"""

MARK_SQL = "select body -> 'feeds' ->> 'quote_bid' from trading.worker_heartbeats where account = %s"


DECISIONS_SQL = """
select day, book, action, trim_scale(weight)::text, trim_scale(previous_weight)::text, notes, attempts, outcome, decided_at, executed_at
from trading.exec_strategy_decisions where account = %s
order by day desc, book limit %s
"""

RISK_EVENTS_SQL = """
select at, book, kind, detail, epoch, trim_scale(round(equity, 2))::text, trim_scale(round(hwm, 2))::text
from trading.exec_risk_events where account = %s
order by id desc limit %s
"""

REQUESTS_SQL = """
select id, kind, actor, reason, requested_at, handled_at, outcome
from trading.exec_operator_requests where account = %s
order by id desc limit %s
"""

ADD_REQUEST_SQL = """
insert into trading.exec_operator_requests (account, kind, actor, reason) values (%s, %s, %s, %s) returning id
"""
CONTROL_LIMIT = 20


class Unavailable(Exception):
    """The P3 tables have not been created yet."""


class WorkerStore(Protocol):
    async def worker_rows(self) -> list[tuple[Any, ...]]: ...
    async def activity_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]: ...
    async def pnl_rows(self, account: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], str | None]: ...
    async def control_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], ...]: ...
    async def add_request(self, account: str, kind: str, actor: str, reason: str) -> int: ...


class NoWorkers:
    async def worker_rows(self) -> list[tuple[Any, ...]]:
        raise Unavailable

    async def activity_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        raise Unavailable

    async def pnl_rows(self, account: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], str | None]:
        raise Unavailable

    async def control_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], ...]:
        raise Unavailable

    async def add_request(self, account: str, kind: str, actor: str, reason: str) -> int:
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


    async def pnl_rows(self, account: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], str | None]:
        try:
            async with self.pool.connection() as conn:
                balances = list(await (await conn.execute(BALANCES_SQL, (list(PNL_ASSETS), account))).fetchall())
                books = list(await (await conn.execute(PNL_SQL, (account,))).fetchall())
                mark = await (await conn.execute(MARK_SQL, (account,))).fetchone()
                return balances, books, None if mark is None else mark[0]
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e

    async def control_rows(self, account: str, limit: int) -> tuple[list[tuple[Any, ...]], ...]:
        try:
            async with self.pool.connection() as conn:
                return tuple([list(await (await conn.execute(sql, (account, limit))).fetchall())
                              for sql in (DECISIONS_SQL, RISK_EVENTS_SQL, REQUESTS_SQL)])
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e

    async def add_request(self, account: str, kind: str, actor: str, reason: str) -> int:
        try:
            async with self.pool.connection() as conn:
                row = await (await conn.execute(ADD_REQUEST_SQL, (account, kind, actor, reason))).fetchone()
                assert row is not None
                return int(row[0])
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


def _txt(d: Decimal | None, places: int | None = None) -> str | None:
    if d is None:
        return None
    if places is not None:
        d = d.quantize(Decimal(1).scaleb(-places))
    n = d.normalize()
    if n == 0:
        return "0"
    return format(n.quantize(Decimal(1)) if n == n.to_integral_value() else n, "f")


def pnl_items(balances: list[tuple[Any, ...]], books: list[tuple[Any, ...]], mark_text: str | None) -> dict[str, Any]:
    """Balances at the epoch's opening vs now, and each book's trading result in USDT (testnet-development-plan.md §8.6).

    BTC is marked at the latest Testnet bid the worker saw, or the last trade price when that is missing. A book's
    result is its USDT flow (sells minus buys minus USDT fees) plus its net BTC (buys minus sells minus BTC fees) at
    that mark. Fees paid in BNB are listed apart and not converted. The epoch change covers everything that moved
    the BTC and USDT balances, including activity outside the bot.
    """
    last_trade = next((r[7] for r in books if r[7] is not None), None)
    mark = Decimal(mark_text) if mark_text else last_trade
    epoch = opened = None
    rows = []
    change = Decimal(0) if mark is not None and balances else None
    for ep, at, asset, opening, now in balances:
        epoch, opened = ep, at
        opening, now = Decimal(opening), (None if now is None else Decimal(now))
        diff = None if now is None else now - opening
        rows.append({"asset": asset, "opening": _txt(opening), "now": _txt(now), "change": _txt(diff)})
        if change is not None and diff is not None and mark is not None:
            change += diff if asset == "USDT" else diff * mark
    out_books = []
    for book, buys, sells, base_net, quote_net, bnb_fees, volume, _last in books:
        pnl = None if mark is None else Decimal(quote_net) + Decimal(base_net) * mark
        out_books.append({"book": book or None, "buys": buys, "sells": sells, "btc_net": _txt(Decimal(base_net)),
                          "usdt_net": _txt(Decimal(quote_net), 8), "bnb_fees": _txt(Decimal(bnb_fees)), "volume": _txt(Decimal(volume), 2),
                          "pnl_usdt": _txt(pnl, 4)})
    return {"epoch": epoch, "opened_at": _iso(opened), "mark": _txt(mark), "mark_source": "quote" if mark_text else "last_trade" if mark else None,
            "balances": rows, "equity_change_usdt": _txt(change, 4), "books": out_books}


def control_items(decisions: list[tuple[Any, ...]], events: list[tuple[Any, ...]], requests: list[tuple[Any, ...]]) -> dict[str, Any]:
    """R10a daily decisions, Guardian risk events and operator requests, newest first."""
    return {
        "decisions": [{"day": str(d[0]), "book": d[1], "action": d[2], "weight": d[3], "previous_weight": d[4], "notes": d[5],
                       "attempts": d[6], "outcome": d[7], "decided_at": _iso(d[8]), "executed_at": _iso(d[9])} for d in decisions],
        "risk_events": [{"at": _iso(e[0]), "book": e[1], "kind": e[2], "detail": e[3], "epoch": e[4], "equity": e[5], "hwm": e[6]}
                        for e in events],
        "requests": [{"id": r[0], "kind": r[1], "actor": r[2], "reason": r[3], "requested_at": _iso(r[4]), "handled_at": _iso(r[5]),
                      "outcome": r[6]} for r in requests],
    }
