"""Testnet P6 tn-r2-v1: one r2 proposal per day on R10a's inputs, mock provider only (testnet-p6-plan.md)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal as D

import numpy as np
import pandas as pd

from trading.analysis import pathA_filter as pf
from trading.analysis import pathA_proposer as pp
from trading.runtime.ai_r2 import ARM_ID, EXPERIMENT_ID, propose_decision

DAY = date(2026, 10, 29)
AT = datetime(2026, 10, 30, 8, tzinfo=UTC)
GRAPH = pp.load_proposer_graph()


def _load(trend: str = "up", funding_out: bool = True):
    days = pd.date_range("2026-06-01", "2026-10-29", freq="D", tz="UTC")
    close = np.linspace(100.0, 200.0, len(days)) if trend == "up" else np.linspace(200.0, 100.0, len(days))
    daily = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=days)
    fdays = days if funding_out else days[:-1]
    funding, est = pd.Series(0.0001, index=fdays), pd.Series(True, index=fdays)

    def load(day: date):
        return daily, funding, est, []
    return load


def _answer(weight: float, **over):
    def decide(req):
        return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "template_id": pp.TEMPLATE_ID,
                "target_weight": weight, "reason_code": "MIXED_CONTEXT", "reason": "mock", "evidence_refs": [],
                "policy_version": pp.PROPOSAL_POLICY} | over
    return pf.MockProvider(decide)


def _run(provider, current=D("0.5"), budget=D("5"), **kw):
    return propose_decision(DAY, current, 0.0, budget, provider, GRAPH, load=_load(**kw), clock=lambda: AT)


def test_an_accepted_proposal_sets_the_weight_and_keeps_r10a_beside_it():
    prov = _answer(0.0)
    d = _run(prov)
    assert d is not None and d.weight == D("0") and d.notes.startswith("AI MIXED_CONTEXT")
    assert d.inputs["code_weight"] == "1.0" and d.inputs["proposal"]["outcome"]["accepted"]
    req = d.inputs["proposal"]["request"]
    assert req["experiment_id"] == EXPERIMENT_ID and req["arm_id"] == ARM_ID
    assert len(prov.calls) == 1 and prov.calls[0]["proposal_id"] == f"{EXPERIMENT_ID}:{DAY}"


def test_rt13_the_snapshot_never_carries_code_weight_and_matches_the_shadow_keys():
    d = _run(_answer(1.0))
    assert d is not None
    ev = d.inputs["snapshot"]["evidence"]
    assert not any("suggested" in k for k in ev)
    assert {"feature:close_perp", "feature:sma100", "feature:trend_up", "feature:funding_7d_per_day",
            "feature:funding_hot", "portfolio:weight", "portfolio:drawdown"} <= set(ev)
    assert sum(k.startswith("close:") for k in ev) == 30 and sum(k.startswith("funding:") for k in ev) == 7
    assert ev["portfolio:weight"] == 0.5 and ev["feature:trend_up"] is True


def test_rt12_failures_keep_the_current_target_and_are_categorised():
    for prov, cat in ((pf.MockProvider(lambda req: None), "TIMEOUT"),
                      (pf.MockProvider(lambda req: "not json"), "INVALID:INVALID_JSON"),
                      (_answer(0.7), "INVALID:INVALID_SCHEMA"),
                      (_answer(1.0, proposal_id="other"), "INVALID:MISMATCHED_IDS")):
        d = _run(prov)
        assert d is not None and d.weight == D("0.5"), cat
        assert d.inputs["proposal"]["outcome"]["error_category"] == cat
        assert "did not answer" in d.notes


def test_rt12_a_failure_before_any_target_leaves_the_day_undecided():
    d = _run(pf.MockProvider(lambda req: None), current=None)
    assert d is not None and d.weight is None and "no target yet" in d.notes


def test_over_budget_no_call_is_made():
    prov = _answer(1.0)
    d = _run(prov, budget=D("0.10"))                     # below the graph's max_run_cost_usd (0.50)
    assert prov.calls == [] and d is not None and d.weight == D("0.5")
    assert d.inputs["proposal"]["outcome"]["error_category"] == "BUDGET_CAP"


def test_waits_like_r10a_and_does_not_ask_while_inputs_are_missing():
    prov = _answer(1.0)
    assert _run(prov, funding_out=False) is None          # the day's funding is not out yet
    assert prov.calls == []


def test_the_stored_record_is_json_and_the_hash_covers_it():
    d = _run(_answer(0.5))
    assert d is not None and len(d.inputs_sha256) == 64
    json.dumps(d.inputs, sort_keys=True)                   # the store writes it as jsonb
