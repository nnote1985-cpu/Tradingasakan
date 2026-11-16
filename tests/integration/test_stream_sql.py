"""SSE cursor query on a throwaway local PostgreSQL (m1-realtime-plan.md §5.1, §5.5).

50 trades committed one by one must each reach a listener as a ``changed`` notice
within 3 s at p95, with the real 1-second read interval.
"""

import asyncio
import statistics
import time
from datetime import UTC, datetime

import pytest

from api.stream import ChangeWatcher, PgCursorSource
from tests.integration.test_bots_api import seed_account
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)

psycopg_pool = pytest.importorskip("psycopg_pool")
psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def seeded(db):  # noqa: F811
    db.execute("truncate trading.exec_trades, trading.exec_order_events, trading.exec_fills, trading.exec_outbox, "
               "trading.exec_reservations, trading.exec_intents, trading.exec_strategy_decisions, trading.exec_risk_events, "
               "trading.exec_operator_requests, trading.worker_heartbeats, trading.exec_reconciliations, "
               "trading.exec_balances, trading.exec_account_epochs cascade")
    seed_account(db, "DEFAULT", body={"safety": "NORMAL", "mode": "TESTNET",
                                      "feeds": {"quote_bid": "95000.10", "quote_ask": "95000.20", "quote_age_ms": 420}})
    return db


def _trade(db, n):  # noqa: F811
    now = datetime.now(UTC)
    db.execute("insert into trading.exec_trades (account, epoch, symbol, trade_id, order_id, client_order_id, side, qty, price, "
               "commission, commission_asset, traded_at, received_at) values ('DEFAULT', 1, 'BTCUSDT', %s, 1, 'c1', 'BUY', "
               "0.001, 95000, 0, 'BNB', %s, %s)", (n, now, now))


def test_cursor_rows_read_every_kind(seeded):
    async def go():
        pool = psycopg_pool.AsyncConnectionPool(seeded.info.dsn, open=False)
        await pool.open()
        try:
            src = PgCursorSource(pool)
            [r] = await src.cursor_rows()
            assert r[0] == "DEFAULT" and r[2:7] == ("95000.10", "95000.20", "420", "NORMAL", "TESTNET")
            assert r[7:] == ("0/", "0/", "0//", "0", "0/0/", "1")
            seeded.execute("insert into trading.exec_operator_requests (account, kind, actor, reason) values ('DEFAULT', 'HALT', 'web:x', 't')")
            seeded.execute("insert into trading.exec_risk_events (account, at, book, kind, detail, epoch) "
                           "values ('DEFAULT', now(), 'b', 'HALT', 't', 1)")
            [r2] = await src.cursor_rows()
            assert r2[11].startswith("1/") and r2[10] != "0"
        finally:
            await pool.close()
    asyncio.run(go())


def test_fifty_trades_reach_a_listener_within_three_seconds_p95(seeded):
    async def go():
        pool = psycopg_pool.AsyncConnectionPool(seeded.info.dsn, open=False)
        await pool.open()
        w = ChangeWatcher(PgCursorSource(pool))          # the real 1 s interval
        q = w.subscribe()
        try:
            ev, _ = await asyncio.wait_for(q.get(), 5)
            assert ev == "hello"
            committed: dict[int, float] = {}
            seen: dict[int, float] = {}

            async def listen():
                while len(seen) < 50:
                    ev, data = await q.get()
                    if ev == "changed" and "trades" in data["kinds"]:
                        t = time.monotonic()
                        for n in committed:
                            seen.setdefault(n, t)

            listener = asyncio.create_task(listen())
            for n in range(1, 51):
                await asyncio.to_thread(_trade, seeded, n)
                committed[n] = time.monotonic()
                await asyncio.sleep(0.1)
            await asyncio.wait_for(listener, 10)
            lat = sorted(seen[n] - committed[n] for n in committed)
            p95 = lat[int(0.95 * len(lat)) - 1]
            assert p95 <= 3.0, (p95, statistics.mean(lat))
        finally:
            w.unsubscribe(q)
            await asyncio.sleep(1.2)
            assert not w.running                           # nobody listens: reads stop
            await pool.close()
    asyncio.run(go())

