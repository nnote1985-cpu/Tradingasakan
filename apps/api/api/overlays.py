"""Bot overlays for the chart (docs/m2-charts-plan.md §4.2): what a Testnet book did and why.

- ``fills``: the account's Binance trades whose order belongs to the book (``exec_intents.book``),
  with the decision that sent the order (``decision_id``; ``halt-…`` = the Guardian flattening);
  another book's trades on the same account (for example ``tn-conformance-v1``) are left out;
- ``decisions``: every ``exec_strategy_decisions`` row of the book (action, weights, notes);
- ``halts``: HALT → RESUME periods of the account (Guardian events carry no book), open when no
  RESUME has come yet; ``alerts`` are the book's other risk events (drawdown, day loss).

Quantities and prices leave as decimal strings; the browser only formats them.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

FILLS_SQL = """
select t.traded_at, t.side, t.qty, t.price, t.commission, t.commission_asset, t.client_order_id, i.decision_id, i.mode
from trading.exec_trades t join trading.exec_intents i on i.client_order_id = t.client_order_id
where i.account = %s and i.book = %s and t.traded_at >= %s and t.traded_at < %s
order by t.traded_at, t.trade_id
"""
DECISIONS_SQL = """
select day, rules_version, action, weight, previous_weight, notes, inputs_sha256, decided_at, client_order_id, outcome
from trading.exec_strategy_decisions
where account = %s and book = %s and day >= %s and day < %s
order by day
"""
EVENTS_SQL = """
select at, kind, book, detail from trading.exec_risk_events
where account = %s and (book is null or book = %s) and at < %s
order by at, id
"""
HALT_KINDS = {"HALT", "RESUME", "FLAT_CONFIRMED"}


class OverlayStore(Protocol):
    async def rows(self, account: str, book: str, start: datetime, end: datetime) -> tuple[list[tuple[Any, ...]], ...]: ...


class Unavailable(RuntimeError):
    pass


class NoOverlays:
    async def rows(self, account: str, book: str, start: datetime, end: datetime) -> tuple[list[tuple[Any, ...]], ...]:
        raise Unavailable


class PgOverlayStore:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def rows(self, account: str, book: str, start: datetime, end: datetime) -> tuple[list[tuple[Any, ...]], ...]:
        from psycopg import errors as pg_errors
        try:
            async with self.pool.connection() as conn:
                fills = list(await (await conn.execute(FILLS_SQL, (account, book, start, end))).fetchall())
                decisions = list(await (await conn.execute(DECISIONS_SQL, (account, book, start.date(), end.date()))).fetchall())
                events = list(await (await conn.execute(EVENTS_SQL, (account, book, end))).fetchall())
                return fills, decisions, events
        except pg_errors.UndefinedTable as e:
            raise Unavailable from e


def _d(v: Any) -> str | None:
    if v is None:
        return None
    return format(Decimal(v).normalize(), "f")


def _iso(v: datetime | date | None) -> str | None:
    return None if v is None else v.isoformat()


def _t(v: datetime) -> int:
    return int(v.timestamp())


def overlay_items(fills: list[tuple[Any, ...]], decisions: list[tuple[Any, ...]], events: list[tuple[Any, ...]],
                  start: datetime) -> dict[str, Any]:
    by_order = {}
    out_fills = []
    for at, side, qty, price, fee, fee_asset, coid, decision_id, mode in fills:
        kind = "halt" if str(decision_id).startswith("halt-") else "strategy"
        out_fills.append({"t": _t(at), "at": _iso(at), "side": side, "qty": _d(qty), "price": _d(price), "fee": _d(fee),
                          "fee_asset": fee_asset, "client_order_id": coid, "decision_id": decision_id, "kind": kind, "mode": mode})
        by_order[coid] = decision_id
    out_dec = []
    for day, rules, action, weight, prev, notes, sha, decided_at, coid, outcome in decisions:
        out_dec.append({"day": str(day), "t": _t(datetime.combine(day, datetime.min.time(), decided_at.tzinfo)),
                        "decided_at": _iso(decided_at), "rules_version": rules, "action": action,
                        "weight": _d(weight), "previous_weight": _d(prev), "notes": notes, "inputs_sha256": sha,
                        "client_order_id": coid, "outcome": outcome, "filled": coid in by_order})
    halts: list[dict[str, Any]] = []
    alerts = []
    for at, kind, book, detail in events:
        if kind == "HALT":
            if not halts or halts[-1]["to"] is not None:
                halts.append({"from": _iso(at), "t_from": _t(at), "to": None, "t_to": None, "flat_at": None, "reason": _reason(detail),
                              "resume_reason": None})
        elif kind == "FLAT_CONFIRMED" and halts and halts[-1]["to"] is None:
            halts[-1]["flat_at"] = _iso(at)
        elif kind == "RESUME" and halts and halts[-1]["to"] is None:
            halts[-1].update(to=_iso(at), t_to=_t(at), resume_reason=_reason(detail))
        elif kind not in HALT_KINDS and at >= start:
            alerts.append({"t": _t(at), "at": _iso(at), "kind": kind, "book": book, "detail": detail})
    halts = [h for h in halts if h["t_to"] is None or h["t_to"] >= _t(start)]
    return {"mode": "TESTNET", "fills": out_fills, "decisions": out_dec, "halts": halts, "alerts": alerts}


def _reason(detail: Any) -> str | None:
    if isinstance(detail, dict):
        r = detail.get("reason") or detail.get("note")
        return None if r is None else str(r)
    return None if detail is None else str(detail)
