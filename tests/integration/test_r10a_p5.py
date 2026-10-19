"""Testnet plan P5: the tn-r10a-code-v1 bot on a throwaway local PostgreSQL and an in-memory Binance (§5.1)."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from tests.integration.test_executor_p4 import Rig
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.execution.safety import SafetyState
from trading.runtime.executor import Executor
from trading.runtime.guardian import GuardedBook, Guardian
from trading.runtime.r10a import ALLOCATION, BOOK, Decision, R10aBot, decide
from trading.strategies.sma_band import band_state

pytest.importorskip("psycopg")


class SyncPool:
    """Runs the download at once, so a test sees the result on the next tick."""

    def submit(self, fn, *args):
        f: Future = Future()
        try:
            f.set_result(fn(*args))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    def shutdown(self, **_kw) -> None:
        pass


class BotRig(Rig):
    def __init__(self, db) -> None:  # noqa: F811
        self.weights: dict[date, D | None] = {}
        self.fetched: list[date] = []
        Rig._day[0] += 30                                              # client ids are global: keep each test's days apart
        super().__init__(db, conformance=False)

    def fetch(self, day: date) -> Decision | None:
        self.fetched.append(day)
        if day not in self.weights:
            return None
        w = self.weights[day]
        return Decision(day, w, f"test weight {w}", {"w": None if w is None else str(w)}, "sha", self.t)

    def make(self, conformance: bool = False) -> Executor:
        ex = super().make(conformance=False)
        # Every test account shares the same book name, so it only ever trades on its own (unique) days.
        ex.r10a = R10aBot(self.store, fetch=self.fetch, pool=SyncPool())  # type: ignore[arg-type]
        ex.guardian = Guardian(self.store, [GuardedBook(BOOK, ALLOCATION)])
        return ex

    @property
    def day(self) -> date:
        return self.t.date() - timedelta(days=1)

    def decision(self, day: date | None = None) -> dict:
        row = self.store.load_decision(BOOK, day or self.day)
        assert row is not None
        return row

    def held(self) -> D:
        return self.store.position(BOOK, "BTCUSDT", "BTC", "USDT")[0]

    def next_day(self) -> None:
        self.advance(86400)


@pytest.fixture
def b(db):  # noqa: F811
    r = BotRig(db)
    r.executor.start(r.token)
    r.tick()
    assert r.executor.safety.state is SafetyState.RUNNING
    return r


def test_waits_until_0010_utc_then_retries_every_30_minutes_until_the_archive_has_the_day(b):
    b.tick(3)
    assert b.fetched == []                                             # 00:00:05 + a few seconds: too early
    b.advance(600)
    b.tick(3)
    assert b.fetched == [b.day] and b.store.load_decision(BOOK, b.day) is None
    b.advance(1700)
    b.tick(3)
    assert len(b.fetched) == 1                                         # 30 minutes have not passed
    b.weights[b.day] = D("1")
    b.advance(200)
    b.tick(3)
    assert len(b.fetched) == 2 and b.decision()["action"] == "OPEN"


def test_open_hold_rebalance_and_missing_inputs(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.tick(4)
    d = b.decision()
    assert d["action"] == "OPEN" and d["attempts"] == 1 and d["outcome"].startswith("BUY")
    held = b.held()
    assert held * b.ex.bid > D("1990") and [o.side for o in b.ex.orders.values()] == ["BUY"]

    b.next_day()
    b.weights[b.day] = D("1")
    b.tick(4)
    assert b.decision()["action"] == "HOLD" and len(b.ex.orders) == 1     # unchanged target: no trade

    b.next_day()
    b.weights[b.day] = D("0.5")
    b.tick(4)
    d = b.decision()
    assert d["action"] == "REBALANCE" and d["previous_weight"] == D("1") and d["outcome"].startswith("SELL")
    assert abs(b.held() - held / 2) < D("0.0002")

    b.next_day()
    b.weights[b.day] = None
    b.tick(4)
    assert b.decision()["action"] == "NO_DECISION" and len(b.ex.orders) == 2   # position kept

    b.next_day()
    b.weights[b.day] = D("0")
    b.tick(4)
    assert b.decision()["previous_weight"] == D("0.5") and b.held() < D("0.00001")
    body = b.executor.body()["r10a"]
    assert body["decision"]["action"] == "REBALANCE" and body["decision"]["weight"] == "0.000000"


def test_a_buy_waits_while_entries_are_blocked_and_resumes_after_a_halt(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.store.request("HALT", "note", "drill before the first decision")
    b.tick(4)
    assert b.executor.safety.state is SafetyState.HALTED
    assert b.decision()["action"] == "OPEN" and b.decision()["attempts"] == 0 and b.decision()["executed_at"] is None
    b.store.request("RESUME", "note", "drill done")
    b.advance(10)
    b.tick(4)
    assert b.executor.safety.state is SafetyState.RUNNING
    assert b.decision()["outcome"].startswith("BUY")

    b.store.request("HALT", "note", "second drill")                    # Guardian sells the book
    b.tick(4)
    assert b.executor.safety.state is SafetyState.HALTED and b.held() < D("0.00001")
    b.store.request("RESUME", "note", "back")
    b.advance(10)
    b.tick(3)
    b.next_day()
    b.weights[b.day] = D("1")                                          # same target, but the book is flat now
    b.tick(4)
    d = b.decision()
    assert d["action"] == "REBALANCE" and "does not match" in d["notes"] and d["outcome"].startswith("BUY")


def test_an_order_that_ends_without_a_fill_is_tried_again_up_to_five_times(b):
    b.advance(600)
    b.weights[b.day] = D("1")
    b.ex.plan(*["reject"] * 5)
    b.tick(12)
    d = b.decision()
    assert d["attempts"] == 5 and d["outcome"] == "gave up after 5 orders without a fill" and b.held() == 0
    assert any(k == "r10a-trade" for _, k, _ in b.alerts.items)


def test_decide_matches_the_frozen_band_rule():
    days = pd.date_range("2025-01-01", periods=160, freq="1D", tz="UTC")
    close = pd.Series(np.concatenate([np.linspace(100, 80, 100), np.linspace(80, 130, 60)]), index=days)
    daily = pd.DataFrame({"close": close})
    funding = pd.Series(0.0001, index=days)
    est = pd.Series(False, index=days)
    at = datetime(2025, 7, 1, tzinfo=UTC)
    for i in (110, 130, 159):
        d = decide(daily, funding, est, days[i].date(), at)
        assert d is not None and d.weight == D(str(band_state(close.iloc[: i + 1], signed=False).iloc[-1]))
    hot = decide(daily, pd.Series(0.002, index=days), est, days[159].date(), at)
    assert hot is not None and hot.weight == D("0.5") and hot.inputs["funding_hot"] is True
    assert decide(daily, funding, est, (days[-1] + timedelta(days=1)).date(), at) is None    # the archive lacks the day
    short = decide(daily.iloc[:50], funding, est, days[49].date(), at)
    assert short is not None and short.weight is None                                      # SMA100 not there yet
