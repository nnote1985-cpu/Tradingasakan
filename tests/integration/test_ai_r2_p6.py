"""Testnet P6: the tn-r2-v1 AI book on a throwaway local PostgreSQL and an in-memory Binance, mock AI only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from tests.integration.test_executor_p4 import Rig
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from tests.integration.test_r10a_p5 import SyncPool
from trading.analysis import pathA_filter as pf
from trading.analysis import pathA_proposer as pp
from trading.execution.safety import SafetyState
from trading.runtime.ai_r2 import BOOK, AiR2Bot
from trading.runtime.guardian import GuardedBook, Guardian
from trading.runtime.r10a import ALLOCATION

pytest.importorskip("psycopg")


class AiRig(Rig):
    def __init__(self, db, answers: list) -> None:  # noqa: F811
        self.answers = answers                 # one target weight (or None = no answer) per call
        Rig._day[0] += 30
        super().__init__(db, conformance=False)

    def load(self, day: date):
        days = pd.date_range(end=pd.Timestamp(day, tz="UTC"), periods=150, freq="D")
        close = np.linspace(100.0, 200.0, len(days))
        daily = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=days)
        return daily, pd.Series(0.0001, index=days), pd.Series(True, index=days), []

    def provider(self) -> pf.MockProvider:
        def decide(req):
            w = self.answers.pop(0)
            if w is None:
                return None
            return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "template_id": pp.TEMPLATE_ID,
                    "target_weight": w, "reason_code": "MIXED_CONTEXT", "reason": "mock", "evidence_refs": [],
                    "policy_version": pp.PROPOSAL_POLICY}
        return pf.MockProvider(decide)

    def make(self, conformance: bool = False):
        ex = super().make(conformance=False)
        ex.r10a = AiR2Bot(self.store, pool=SyncPool(), provider=self.provider(), load=self.load)  # type: ignore[arg-type]
        ex.guardian = Guardian(self.store, [GuardedBook(BOOK, ALLOCATION)])
        return ex

    @property
    def day(self) -> date:
        return self.t.date() - pd.Timedelta(days=1)

    def decision(self) -> dict:
        row = self.store.load_decision(BOOK, self.day)
        assert row is not None
        return row

    def held(self) -> D:
        return self.store.position(BOOK, "BTCUSDT", "BTC", "USDT")[0]


def _rig(db, answers):  # noqa: F811
    r = AiRig(db, answers)
    r.executor.start(r.token)
    r.tick()
    assert r.executor.safety.state is SafetyState.RUNNING
    return r


def test_the_ai_target_is_traded_and_a_missed_answer_keeps_it(db):  # noqa: F811
    r = _rig(db, [1.0, None, 0.0])
    r.advance(600)
    r.tick(4)
    d = r.decision()
    assert d["action"] == "OPEN" and d["weight"] == D("1") and d["outcome"].startswith("BUY")
    assert d["notes"].startswith("AI MIXED_CONTEXT") and r.held() * r.ex.bid > D("1990")
    sent = r.store.load_order(d["client_order_id"])
    assert sent.intent.book == BOOK and sent.intent.decision_id.startswith("air2-")

    r.advance(86400)
    r.tick(4)
    d = r.decision()
    assert d["action"] == "HOLD" and "did not answer (TIMEOUT)" in d["notes"] and len(r.ex.orders) == 1

    r.advance(86400)
    r.tick(4)
    d = r.decision()
    assert d["action"] == "REBALANCE" and d["previous_weight"] == D("1") and d["outcome"].startswith("SELL")
    assert r.held() < D("0.00001")
    assert r.store.ai_spend(BOOK, r.day.replace(day=1)) >= 0        # mock calls are priced as unknown models: 0 here
    body = r.executor.body()["r10a"]
    assert body["book"] == BOOK and body["decision"]["action"] == "REBALANCE"


def test_no_answer_on_the_first_day_leaves_the_book_undecided_and_flat(db):  # noqa: F811
    r = _rig(db, [None])
    r.advance(600)
    r.tick(4)
    d = r.decision()
    assert d["action"] == "NO_DECISION" and r.ex.orders == {}
