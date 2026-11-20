"""Futures desk view (futures-desk-plan.md §6): GET /api/v1/futures/desk.

Reads only what the futures worker writes (trading.fut_*). Nothing here talks to
Binance or places orders. Before the migration is applied the tables do not
exist and the endpoint answers ``"available": false``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool

from trading.futures import rules
from trading.futures.risk import caps, position_risk
from trading.futures.sync import RiskState
from trading.futures.trades import round_trips

STALE_AFTER = timedelta(seconds=60)    # the worker syncs about every 10 s
TRADE_LIMIT = 200
HISTORY = timedelta(days=90)

STATE_SQL = """
select s.account, s.synced_at, s.wallet_balance, s.margin_balance, s.available_balance, s.unrealized_pnl,
       s.positions, s.open_orders, s.markets, s.body,
       r.day, r.day_open, r.hwm, r.hwm_at, r.start_equity, r.started_at
from trading.fut_state s left join trading.fut_risk_state r on r.account = s.account
order by s.account
"""
FILLS_SQL = """
select symbol, trade_id, order_id, side, qty, price, realized_pnl, commission, commission_asset, traded_at
from trading.fut_fills where account = %s and traded_at >= %s order by traded_at, trade_id
"""
INCOME_SQL = """
select kind, symbol, amount, asset, at from trading.fut_income
where account = %s and at >= %s and kind = 'FUNDING_FEE' order by at
"""


class Unavailable(Exception):
    """The futures desk tables have not been created yet."""


class FuturesStore(Protocol):
    async def state_rows(self) -> list[tuple[Any, ...]]: ...
    async def history(self, account: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


class NoFutures:
    async def state_rows(self) -> list[tuple[Any, ...]]:
        raise Unavailable

    async def history(self, account: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raise Unavailable


class PgFuturesStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def state_rows(self) -> list[tuple[Any, ...]]:
        try:
            async with self.pool.connection() as conn:
                cur = await conn.execute(STATE_SQL)
                return list(await cur.fetchall())
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e

    async def history(self, account: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            async with self.pool.connection() as conn:
                fills = await (await conn.execute(FILLS_SQL, (account, since))).fetchall()
                income = await (await conn.execute(INCOME_SQL, (account, since))).fetchall()
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e
        keys = ("symbol", "trade_id", "order_id", "side", "qty", "price", "realized_pnl", "commission", "commission_asset", "traded_at")
        return ([dict(zip(keys, r, strict=True)) for r in fills],
                [dict(zip(("kind", "symbol", "amount", "asset", "at"), r, strict=True)) for r in income])


def _txt(v: Any) -> Any:
    """Decimals as plain strings (no exponent, no trailing zeros); containers recursively."""
    if isinstance(v, Decimal):
        t = format(v.normalize(), "f") if v == v else "0"
        return "0" if t == "-0" else t
    if isinstance(v, dict):
        return {k: _txt(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_txt(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _q(v: Decimal | None, places: str = "0.01") -> Decimal | None:
    return None if v is None else v.quantize(Decimal(places))


def summary(trades: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    closed = [t for t in trades if not t["open"]]
    wins = [t for t in closed if t["net_pnl"] > 0]
    today = now.astimezone(UTC).date().isoformat()
    return {
        "count": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
        "wins": len(wins), "losses": len(closed) - len(wins),
        "win_rate": _q(Decimal(len(wins)) / len(closed), "0.0001") if closed else None,
        "net_pnl": _q(sum((t["net_pnl"] for t in closed), Decimal(0))),
        "fees": _q(sum((t["fees"] for t in trades), Decimal(0))),
        "funding": _q(sum((t["funding"] for t in trades), Decimal(0))),
        "today_net": _q(sum((t["net_pnl"] for t in closed if (t["closed_at"] or "").startswith(today)), Decimal(0))),
        "avg_win": _q(sum((t["net_pnl"] for t in wins), Decimal(0)) / len(wins)) if wins else None,
        "avg_loss": _q(sum((t["net_pnl"] for t in closed if t["net_pnl"] <= 0), Decimal(0)) / (len(closed) - len(wins)))
        if len(closed) > len(wins) else None,
    }


def desk_view(row: tuple[Any, ...], fills: list[dict[str, Any]], income: list[dict[str, Any]],
              accounts: list[str], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    (account, synced_at, wallet, equity, avail, upnl, positions, orders, markets, body,
     day, day_open, hwm, hwm_at, start_equity, started_at) = row
    equity = Decimal(equity)
    risk = RiskState(day, Decimal(day_open), Decimal(hwm), hwm_at, Decimal(start_equity), started_at) if day else None
    pos = []
    for p in positions:
        r = position_risk(p, orders, equity)
        pos.append(p | {k: (_q(v, "0.0001") if k.endswith("_pct") and isinstance(v, Decimal) else v) for k, v in r.items()})
    trades = [t.view() for t in round_trips(fills, income)]
    for t in trades:
        for k in ("entry_price", "exit_price"):
            t[k] = _q(t[k], "0.0001")
        for k in ("realized_pnl", "fees", "funding", "net_pnl"):
            t[k] = _q(t[k], "0.0001")
        t["return_pct"] = _q(t["net_pnl"] / equity, "0.0001") if equity > 0 else None
    out = {
        "available": True, "accounts": accounts, "account": account, "venue": "BINANCE_USDM_DEMO",
        "synced_at": synced_at.isoformat(), "stale": now - synced_at > STALE_AFTER,
        "worker": {"orders_enabled": bool(body.get("orders_enabled")), "last_error": body.get("last_error"),
                   "started_at": body.get("started_at"), "host": body.get("host")},
        "equity": equity, "wallet_balance": wallet, "available_balance": avail, "unrealized_pnl": upnl,
        "rules": {"risk_per_trade": rules.RISK_PER_TRADE, "day_loss_cap": rules.DAY_LOSS_CAP,
                  "total_loss_cap": rules.TOTAL_LOSS_CAP, "symbols": list(rules.SYMBOLS)},
        "caps": caps(equity, risk) if risk else None,
        "start_equity": start_equity, "started_at": started_at.isoformat() if started_at else None,
        "positions": pos, "orders": orders, "markets": markets,
        "unprotected": [p["symbol"] for p in pos if not p["has_stop"]],
        "trades": list(reversed(trades))[:TRADE_LIMIT],
        "summary": summary(trades, now),
    }
    if out["caps"]:
        for side in ("day", "total"):
            c = out["caps"][side]
            for k in ("loss", "used"):
                c[k] = _q(c[k], "0.0001")
            c["room"] = _q(c["room"])
    view: dict[str, Any] = _txt(out)
    return view


async def desk(store: FuturesStore, account: str | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    try:
        rows = await store.state_rows()
    except Unavailable:
        return {"available": False, "reason": "no_tables", "accounts": []}
    accounts = [r[0] for r in rows]
    if not rows:
        return {"available": False, "reason": "no_worker", "accounts": []}
    row = next((r for r in rows if r[0] == account), rows[0]) if account else rows[0]
    fills, income = await store.history(row[0], now - HISTORY)
    return desk_view(row, fills, income, accounts, now)
