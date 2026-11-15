"""M3: tn-r10a-code-v2 (TargetBot + planner) on a throwaway local PostgreSQL and an in-memory Binance.

The same scenarios as the v1.1 bot (test_r10a_p5.py), so the two state machines are held to the same behaviour.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from tests.integration.test_executor_p4 import Rig
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from tests.integration.test_r10a_p5 import SyncPool
from trading.contracts.strategy import Market, TargetDecision
from trading.execution.safety import SafetyState
from trading.runtime.executor import Executor
from trading.runtime.guardian import GuardedBook, Guardian
from trading.runtime.target_bot import r10a_v2_bot

pytest.importorskip("psycopg")
BOOK = "tn-r10a-code-v2"


class V2Rig(Rig):
    def __init__(self, db) -> None:  # noqa: F811
        self.weights: dict[date, D | None] = {}
        Rig._day[0] += 30                                              # client ids are global: keep each test's days apart
        super().__init__(db, conformance=False)

    def fetch(self, day: date) -> TargetDecision | None:
        if day not in self.weights:
            return None
        w = self.weights[day]
        return TargetDecision.make(strategy_version="tn-r10a-code-v2", instrument="BTCUSDT", market=Market.SPOT, day=day,
                                   target_weight=w, reason=f"test weight {w}", inputs={"w": None if w is None else str(w)},
                                   data_ready_at=self.t)

    def make(self, conformance: bool = False) -> Executor:
        ex = super().make(conformance=False)
        ex.r10a = r10a_v2_bot(self.store, fetch=self.fetch, pool=SyncPool())  # type: ignore[assignment,arg-type]
        ex.guardian = Guardian(self.store, [GuardedBook(BOOK, D("2000"))])
        return ex

    @property
    def day(self) -> date:
        return self.t.date() - timedelta(days=1)

    def decision(self) -> dict:
        row = self.store.load_decision(BOOK, self.day)
        assert row is not None
        return row

    def held(self) -> D:
        return self.store.position(BOOK, "BTCUSDT", "BTC", "USDT")[0]

    def intents(self) -> list[tuple[str, str, bool]]:
        rows = self.store.conn.execute("select decision_id, side, reduce_only from trading.exec_intents where book = %s "
                                       "and account = %s order by created_at", (BOOK, self.store.account)).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def next_day(self) -> None:
        self.advance(86400)


@pytest.fixture
def b(db):  # noqa: F811
    r = V2Rig(db)
    r.executor.start(r.token)
    r.tick()
    assert r.executor.safety.state is SafetyState.RUNNING
    return r


def test_open_hold_rebalance_missing_and_flat(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.tick(4)
    d = b.decision()
    assert d["action"] == "OPEN" and d["outcome"].startswith("BUY")
    assert b.store.conn.execute("select rules_version from trading.exec_strategy_decisions where book = %s and day = %s",
                                (BOOK, b.day)).fetchone()[0] == "tn-r10a-code-v2"
    held = b.held()
    assert held * b.ex.bid > D("1990")
    assert b.intents() == [(f"r10a2-{b.day}", "BUY", False)]

    b.next_day()
    b.weights[b.day] = D("1")
    b.tick(4)
    assert b.decision()["action"] == "HOLD" and len(b.ex.orders) == 1

    b.next_day()
    b.weights[b.day] = D("0.5")
    b.tick(4)
    d = b.decision()
    assert d["action"] == "REBALANCE" and d["outcome"].startswith("SELL") and abs(b.held() - held / 2) < D("0.0002")
    assert [x[1:] for x in b.intents()] == [("BUY", False), ("SELL", True)]

    b.next_day()
    b.weights[b.day] = None
    b.tick(4)
    assert b.decision()["action"] == "NO_DECISION" and len(b.ex.orders) == 2

    b.next_day()
    b.weights[b.day] = D("0")
    b.tick(4)
    assert b.decision()["previous_weight"] == D("0.5") and b.held() < D("0.00001")
    assert b.executor.body()["r10a"]["strategy"] == "tn-r10a-code-v2"


def test_halt_resume_and_off_target_rebalance(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.store.request("HALT", "note", "drill before the first decision")
    b.tick(4)
    assert b.decision()["action"] == "OPEN" and b.decision()["executed_at"] is None
    b.store.request("RESUME", "note", "drill done")
    b.advance(10)
    b.tick(4)
    assert b.decision()["outcome"].startswith("BUY")
    b.store.request("HALT", "note", "second drill")                    # Guardian sells the book
    b.tick(4)
    assert b.held() < D("0.00001")
    b.store.request("RESUME", "note", "back")
    b.advance(10)
    b.tick(3)
    b.next_day()
    b.weights[b.day] = D("1")
    b.tick(4)
    d = b.decision()
    assert d["action"] == "REBALANCE" and "does not match" in d["notes"] and d["outcome"].startswith("BUY")


def test_five_attempts_without_a_fill(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.ex.plan(*["reject"] * 5)
    b.tick(12)
    d = b.decision()
    assert d["attempts"] == 5 and d["outcome"] == "gave up after 5 orders without a fill" and b.held() == 0
