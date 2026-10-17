"""P1: two workers on one account against the real writer-lease table (RT07)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.config import Settings
from trading.execution.store import ExecStore
from trading.runtime.alerts import AlertQueue
from trading.runtime.credentials import TestnetCredentials
from trading.runtime.worker import LeaseLost, TestnetRest, Worker, WorkerConfig

pytest.importorskip("psycopg")
TESTNET = Settings(trading_mode="TESTNET", testnet_execution_enabled=True)


def _http(url, headers):
    return (200, {"serverTime": 0}) if "/time" in url else (200, {"canTrade": True})


def test_only_one_worker_holds_the_account(db, tmp_path):  # noqa: F811
    account = f"acct-{datetime.now(UTC).timestamp()}"
    workers = []
    for name in ("pc", "vps"):
        q = AlertQueue(tmp_path / name / "alerts.jsonl", None, name, lambda: datetime.now(UTC), log=lambda m: None)
        rest = TestnetRest(TestnetCredentials("k", secret="s"), get=_http, wall_ms=lambda: 0.0)
        workers.append(Worker(TESTNET, WorkerConfig(owner=name, heartbeat_path=tmp_path / name / "hb.json"), rest, q,
                              store=ExecStore(db, account), log=lambda m: None))
    pc, vps = workers
    pc.start()
    pc.tick()
    with pytest.raises(LeaseLost):
        vps.start()
    assert pc.state.lease_token == 1 and pc.state.safety == "RECOVERING"
