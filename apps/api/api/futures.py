"""Futures desk (futures-desk-plan.md §6, §7): the view and the manual order tickets.

Reads what the futures worker writes (trading.fut_*). Nothing here talks to Binance:
a ticket the checks accept becomes a row in trading.fut_order_requests, which the
worker checks again on fresh data and carries out. Before the migration is applied
the tables do not exist and the view answers ``"available": false``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from trading.futures import rules
from trading.futures.orders import MAX_LEVERAGE, Plan, Ticket, check_move_stop, plan_open
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
REQUESTS_SQL = """
select id, kind, symbol, side, stop_price, take_profit, leverage, qty, risk_usdt, actor, requested_at, status, handled_at, outcome
from trading.fut_order_requests where account = %s order by id desc limit 20
"""
INSERT_SQL = """
insert into trading.fut_order_requests (account, kind, symbol, side, stop_price, take_profit, leverage, qty, ref_price, risk_usdt,
                                        actor, request_id, detail)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (request_id) do nothing
returning id
"""
INCOME_SQL = """
select kind, symbol, amount, asset, at from trading.fut_income
where account = %s and at >= %s and kind = 'FUNDING_FEE' order by at
"""


class Unavailable(Exception):
    """The futures desk tables have not been created yet."""


class Refused(Exception):
    """A ticket the checks refuse; ``reasons`` are Thai sentences for the page."""

    def __init__(self, status: int, code: str, reasons: list[str], plan: dict[str, Any] | None = None) -> None:
        super().__init__(reasons[0] if reasons else code)
        self.status, self.code, self.reasons, self.plan = status, code, reasons, plan


class OpenTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str | None = None
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    side: Literal["LONG", "SHORT"]
    stop_price: Decimal = Field(gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    leverage: int = Field(ge=1, le=MAX_LEVERAGE)
    request_id: uuid.UUID | None = None


class CloseTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str | None = None
    request_id: uuid.UUID


class StopTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str | None = None
    stop_price: Decimal = Field(gt=0)
    request_id: uuid.UUID


class FuturesStore(Protocol):
    async def state_rows(self) -> list[tuple[Any, ...]]: ...
    async def history(self, account: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...
    async def requests(self, account: str) -> list[dict[str, Any]]: ...
    async def add_request(self, account: str, kind: str, symbol: str, actor: str, request_id: uuid.UUID,
                          fields: dict[str, Any]) -> int | None: ...


class NoFutures:
    async def state_rows(self) -> list[tuple[Any, ...]]:
        raise Unavailable

    async def history(self, account: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raise Unavailable

    async def requests(self, account: str) -> list[dict[str, Any]]:
        raise Unavailable

    async def add_request(self, account: str, kind: str, symbol: str, actor: str, request_id: uuid.UUID,
                          fields: dict[str, Any]) -> int | None:
        raise Unavailable


REQUEST_KEYS = ("id", "kind", "symbol", "side", "stop_price", "take_profit", "leverage", "qty", "risk_usdt", "actor",
                "requested_at", "status", "handled_at", "outcome")


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

    async def requests(self, account: str) -> list[dict[str, Any]]:
        try:
            async with self.pool.connection() as conn:
                rows = await (await conn.execute(REQUESTS_SQL, (account,))).fetchall()
        except pg_errors.UndefinedTable:
            return []                # stage 2 migration not applied yet
        return [dict(zip(REQUEST_KEYS, r, strict=True)) for r in rows]

    async def add_request(self, account: str, kind: str, symbol: str, actor: str, request_id: uuid.UUID,
                          fields: dict[str, Any]) -> int | None:
        f = fields
        try:
            async with self.pool.connection() as conn:
                row = await (await conn.execute(INSERT_SQL, (
                    account, kind, symbol, f.get("side"), f.get("stop_price"), f.get("take_profit"), f.get("leverage"),
                    f.get("qty"), f.get("ref_price"), f.get("risk_usdt"), actor, request_id, Jsonb(f.get("detail", {}))))).fetchone()
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e
        return int(row[0]) if row else None


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
              accounts: list[str], now: datetime | None = None, requests: list[dict[str, Any]] | None = None,
              can_edit: bool = False) -> dict[str, Any]:
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
        "requests": requests or [],
        "can_trade": can_edit and bool(body.get("orders_enabled")) and now - synced_at <= STALE_AFTER,
        "max_leverage": MAX_LEVERAGE,
    }
    if out["caps"]:
        for side in ("day", "total"):
            c = out["caps"][side]
            for k in ("loss", "used"):
                c[k] = _q(c[k], "0.0001")
            c["room"] = _q(c["room"])
    view: dict[str, Any] = _txt(out)
    return view


async def desk(store: FuturesStore, account: str | None, now: datetime | None = None, can_edit: bool = False) -> dict[str, Any]:
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
    return desk_view(row, fills, income, accounts, now, await store.requests(row[0]), can_edit)


# --- tickets (stage 2) ---------------------------------------------------------

async def _row(store: FuturesStore, account: str | None) -> tuple[Any, ...]:
    try:
        rows = await store.state_rows()
    except Unavailable as e:
        raise Refused(503, "futures_unavailable", ["ยังไม่มีตาราง Futures ในฐานข้อมูล"]) from e
    row = next((r for r in rows if r[0] == account), None) if account else (rows[0] if rows else None)
    if row is None:
        raise Refused(404, "no_account", ["ไม่พบบัญชี Futures นี้ worker ยังไม่ได้เริ่ม"])
    return row


def _plan(row: tuple[Any, ...], t: OpenTicket) -> Plan:
    (_a, _at, _w, equity, avail, _u, positions, orders, markets, _b, day, day_open, hwm, hwm_at, start, started) = row
    risk = RiskState(day, Decimal(day_open), Decimal(hwm), hwm_at, Decimal(start), started) if day else None
    return plan_open(Ticket(t.symbol, t.side, t.stop_price, t.leverage, t.take_profit), equity=Decimal(equity),
                     available=Decimal(avail), positions=positions, orders=orders, markets=markets,
                     caps=caps(Decimal(equity), risk) if risk else None)


def _ready(row: tuple[Any, ...], now: datetime) -> None:
    if not row[9].get("orders_enabled"):
        raise Refused(409, "orders_disabled", ["worker Futures ยังไม่เปิดรับคำสั่ง (FUTURES_ORDERS_ENABLED บน VM)"])
    if now - row[1] > STALE_AFTER:
        raise Refused(409, "stale", ["ข้อมูลบัญชีเก่าเกิน 1 นาที worker อาจหยุด ส่งคำสั่งไม่ได้"])


async def preview(store: FuturesStore, t: OpenTicket) -> dict[str, Any]:
    p = _plan(await _row(store, t.account), t)
    return p.view()


async def submit_open(store: FuturesStore, t: OpenTicket, actor: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _row(store, t.account)
    _ready(row, now)
    p = _plan(row, t)
    if not p.ok:
        raise Refused(422, "ticket_refused", p.refusals, p.view())
    rid = await store.add_request(row[0], "OPEN", t.symbol, actor, t.request_id or uuid.uuid4(), {
        "side": t.side, "stop_price": p.stop_price, "take_profit": p.take_profit, "leverage": t.leverage, "qty": p.qty,
        "ref_price": p.ref_price, "risk_usdt": p.risk_usdt, "detail": {"preview": p.view()}})
    return {"id": rid, "duplicate": rid is None, "plan": p.view()}


async def submit_close(store: FuturesStore, symbol: str, t: CloseTicket, actor: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _row(store, t.account)
    _ready(row, now)
    if not any(p["symbol"] == symbol for p in row[6]) and not any(o["symbol"] == symbol for o in row[7]):
        raise Refused(422, "nothing_to_close", [f"ไม่มี position หรือคำสั่งค้างของ {symbol}"])
    rid = await store.add_request(row[0], "CLOSE", symbol, actor, t.request_id, {})
    return {"id": rid, "duplicate": rid is None}


async def submit_stop(store: FuturesStore, symbol: str, t: StopTicket, actor: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _row(store, t.account)
    _ready(row, now)
    pos = next((p for p in row[6] if p["symbol"] == symbol), None)
    if pos is None:
        raise Refused(422, "no_position", [f"ไม่มี position {symbol}"])
    m = row[8].get(symbol, {})
    stop, refusals = check_move_stop(pos, t.stop_price, Decimal(row[3]), Decimal(str(m.get("mark") or pos["mark_price"])),
                                     Decimal(str(m.get("tick_size") or "0")))
    if refusals:
        raise Refused(422, "ticket_refused", refusals)
    rid = await store.add_request(row[0], "MOVE_STOP", symbol, actor, t.request_id, {"stop_price": stop})
    return {"id": rid, "duplicate": rid is None, "stop_price": str(stop)}
