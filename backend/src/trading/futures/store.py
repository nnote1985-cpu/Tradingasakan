"""Writes for the futures desk (futures-desk-plan.md §4). One autocommit connection, one account."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from trading.execution.store import ExecStore, Lease
from trading.futures.sync import RiskState, Snapshot

VENUE = "BINANCE_USDM_DEMO"


class FuturesStore:
    def __init__(self, conn: psycopg.Connection[Any], account: str) -> None:
        if conn.autocommit is not True:
            raise ValueError("FuturesStore manages its own transactions; pass an autocommit connection")
        self.conn, self.account = conn, account
        self._lease = ExecStore(conn, account)

    def acquire_lease(self, owner: str, now: datetime, ttl: timedelta) -> Lease | None:
        """The same writer lease table as the spot workers, under this desk's own account name."""
        return self._lease.acquire_lease(owner, now, ttl)

    def load_risk(self) -> RiskState | None:
        row = self.conn.execute("select day, day_open, hwm, hwm_at, start_equity, started_at from trading.fut_risk_state "
                                "where account = %s", (self.account,)).fetchone()
        return RiskState(*row) if row else None

    def last_fill_ids(self) -> dict[str, int]:
        rows = self.conn.execute("select symbol, max(trade_id) from trading.fut_fills where account = %s group by symbol",
                                 (self.account,)).fetchall()
        return {s: int(t) for s, t in rows}

    def last_income_at(self) -> datetime | None:
        row = self.conn.execute("select max(at) from trading.fut_income where account = %s", (self.account,)).fetchone()
        return row[0] if row else None

    def save(self, snap: Snapshot, risk: RiskState, body: dict[str, Any], fills: list[dict[str, Any]],
             income: list[dict[str, Any]]) -> tuple[int, int]:
        """State, risk marks, new fills and new income in one transaction. Returns (fills added, income rows added)."""
        a = self.account
        with self.conn.transaction():
            self.conn.execute(
                "insert into trading.fut_state (account, venue, synced_at, wallet_balance, margin_balance, available_balance, "
                "unrealized_pnl, positions, open_orders, markets, body, updated_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) on conflict (account) do update set "
                "synced_at = excluded.synced_at, wallet_balance = excluded.wallet_balance, margin_balance = excluded.margin_balance, "
                "available_balance = excluded.available_balance, unrealized_pnl = excluded.unrealized_pnl, "
                "positions = excluded.positions, open_orders = excluded.open_orders, markets = excluded.markets, "
                "body = excluded.body, updated_at = now()",
                (a, VENUE, snap.synced_at, snap.wallet_balance, snap.margin_balance, snap.available_balance, snap.unrealized_pnl,
                 Jsonb(snap.positions), Jsonb(snap.open_orders), Jsonb(snap.markets), Jsonb(body)))
            self.conn.execute(
                "insert into trading.fut_risk_state (account, day, day_open, hwm, hwm_at, start_equity, started_at, marked_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (account) do update set day = excluded.day, "
                "day_open = excluded.day_open, hwm = excluded.hwm, hwm_at = excluded.hwm_at, marked_at = excluded.marked_at",
                (a, risk.day, risk.day_open, risk.hwm, risk.hwm_at, risk.start_equity, risk.started_at, snap.synced_at))
            nf = ni = 0
            for f in fills:
                cur = self.conn.execute(
                    "insert into trading.fut_fills (account, symbol, trade_id, order_id, side, qty, price, realized_pnl, commission, "
                    "commission_asset, maker, traded_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict do nothing",
                    (a, f["symbol"], f["trade_id"], f["order_id"], f["side"], f["qty"], f["price"], f["realized_pnl"],
                     f["commission"], f["commission_asset"], f["maker"], f["traded_at"]))
                nf += cur.rowcount
            for i in income:
                cur = self.conn.execute(
                    "insert into trading.fut_income (account, tran_id, kind, symbol, amount, asset, info, trade_id, at) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                    (a, i["tran_id"], i["kind"], i["symbol"], i["amount"], i["asset"], i["info"], i["trade_id"], i["at"]))
                ni += cur.rowcount
        return nf, ni
