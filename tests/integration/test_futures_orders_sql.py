"""fut_order_requests on a throwaway local PostgreSQL (futures-desk-plan.md §7): at-most-once and forward-only."""

import asyncio
import uuid
from decimal import Decimal

import pytest

from api.futures import PgFuturesStore
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.futures.store import FuturesStore

psycopg = pytest.importorskip("psycopg")
psycopg_pool = pytest.importorskip("psycopg_pool")


def test_request_moves_forward_once(db):  # noqa: F811
    dsn = f"host={db.info.host} user={db.info.user} dbname={db.info.dbname}"
    rid = uuid.uuid4()

    async def add():
        async with psycopg_pool.AsyncConnectionPool(dsn, open=False) as pool:
            s = PgFuturesStore(pool)
            fields = {"side": "LONG", "stop_price": Decimal("79200"), "leverage": 3, "qty": Decimal("0.125"),
                      "ref_price": Decimal("80000"), "risk_usdt": Decimal("100")}
            first = await s.add_request("FUT9", "OPEN", "BTCUSDT", "web:x", rid, fields)
            again = await s.add_request("FUT9", "OPEN", "BTCUSDT", "web:x", rid, fields)
            return first, again, await s.requests("FUT9")

    first, again, listed = asyncio.run(add())
    assert first is not None and again is None and listed[0]["status"] == "PENDING"
    store = FuturesStore(db, "FUT9")
    [req] = store.open_requests()
    assert req["qty"] == Decimal("0.125")
    assert store.mark_request(req["id"], "SENT") is True
    assert store.mark_request(req["id"], "SENT") is False          # never sent twice
    assert store.mark_request(req["id"], "DONE", "ok", {"entry": {"orderId": 1}}) is True
    assert store.open_requests() == []
    assert store.mark_request(req["id"], "FAILED") is False
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("update trading.fut_order_requests set qty = 9 where id = %s", (req["id"],))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("delete from trading.fut_order_requests")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into trading.fut_order_requests (account, kind, symbol, actor, request_id) "
                   "values ('FUT9', 'OPEN', 'BTCUSDT', 'x', gen_random_uuid())")     # OPEN without a stop
