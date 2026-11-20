"""Futures desk tables on a throwaway local PostgreSQL (futures-desk-plan.md §4): worker writes, API reads."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.futures import PgFuturesStore, desk
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from tests.unit.test_futures_desk import ALGOS, INFO, POS, PREMIUM, FakeRest
from trading.futures.store import FuturesStore
from trading.futures.sync import fill_row, income_row, mark_risk, markets, snapshot

psycopg = pytest.importorskip("psycopg")
psycopg_pool = pytest.importorskip("psycopg_pool")

T0 = datetime(2026, 11, 18, 1, 0, tzinfo=UTC)


def test_store_saves_once_and_api_reads(db):  # noqa: F811
    store = FuturesStore(db, "FUT1")
    assert store.acquire_lease("vm:1", T0, timedelta(seconds=45)).token >= 1
    assert store.acquire_lease("vm:2", T0, timedelta(seconds=45)) is None
    rest = FakeRest()
    snap = snapshot(T0, rest.account(), POS, [], ALGOS, markets(INFO, PREMIUM), {"BTCUSDT": {"marginType": "ISOLATED", "leverage": 3}})
    risk = mark_risk(store.load_risk(), snap.margin_balance, T0)
    fills = [fill_row(t) for t in rest.trades["BTCUSDT"]]
    income = [income_row(i) for i in rest.income(0)]
    assert store.save(snap, risk, {"orders_enabled": False}, fills, income) == (1, 1)
    assert store.save(snap, risk, {"orders_enabled": False}, fills, income) == (0, 0)     # same rows again: no duplicates
    assert store.last_fill_ids() == {"BTCUSDT": 1} and store.last_income_at() is not None
    assert store.load_risk().hwm == snap.margin_balance
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("delete from trading.fut_fills")

    dsn = f"host={db.info.host} user={db.info.user} dbname={db.info.dbname}"

    async def read():
        async with psycopg_pool.AsyncConnectionPool(dsn, open=False) as pool:
            return await desk(PgFuturesStore(pool), "FUT1", now=T0 + timedelta(seconds=5))

    v = asyncio.run(read())
    assert v["available"] and v["accounts"] == ["FUT1"] and v["positions"][0]["stop_price"] == "77000"
    assert v["trades"][0]["open"] and v["trades"][0]["fees"] == "1.58"


def test_browser_roles_cannot_read_futures_tables(db):  # noqa: F811
    for role in ("anon", "authenticated"):
        with db.transaction():
            db.execute(f"set local role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("select * from trading.fut_state")
            raise psycopg.Rollback()
