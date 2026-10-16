"""Phase 5 r2 on path A: acceptance tests declared in docs/phase-5-r2-plan.md."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from trading.analysis import pathA_filter as pf
from trading.analysis import pathA_proposer as pp
from trading.analysis.graphs import load_graph
from trading.contracts.analysis import AgentGraphConfig
from trading.shadow.proposal_book import ProposalBook, random_proposer, random_weights
from trading.shadow.rebalance_book import PathABook

D0 = pd.Timestamp("2026-01-01", tz="UTC")
GRAPH = pp.load_proposer_graph()
INPUTS = {"close_perp": 1.0, "sma100": 0.9, "trend_up": True, "suggested_btc_weight": 0.5, "funding_window": []}


def market(closes):
    rows, prev = [], closes[0]
    for d, c in enumerate(closes):
        path = np.linspace(prev, c, 25)[1:]
        opens = np.r_[prev, path[:-1]]
        for h in range(24):
            rows.append((D0 + pd.Timedelta(days=d, hours=h), opens[h], path[h]))
        prev = c
    hourly = pd.DataFrame(rows, columns=["t", "open", "close"]).set_index("t")
    return pd.DataFrame({"close": hourly["close"].resample("1D").last()}), hourly


def stamps(day):
    close = day + pd.Timedelta(days=1)
    return close + pd.Timedelta(minutes=5), close + pd.Timedelta(minutes=6)


class Clock:
    def __init__(self):
        self.t = D0.to_pydatetime()

    def __call__(self):
        return self.t


def answer(req, **over):
    return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "template_id": pp.TEMPLATE_ID,
            "target_weight": 1.0, "reason_code": "TREND_SUPPORTS_EXPOSURE", "reason": "r",
            "evidence_refs": ["feature:close_perp"], "policy_version": pp.PROPOSAL_POLICY} | over


class Scripted:
    """A provider whose answer is a function of (request, code weight the runner knows)."""

    model_id = "claude-opus-5-5"

    def __init__(self, fn, tokens=(1000, 200)):
        self.fn, self.tokens, self.code_weight, self.users = fn, tokens, None, []

    def ask(self, system, user, schema, max_tokens, timeout_s):
        self.users.append(system + user)
        req = json.loads(user.split("REQUEST:", 1)[1].split("\nSNAPSHOT:", 1)[0])
        out = self.fn(req, self.code_weight)
        if isinstance(out, pf.ProviderReply):
            return out
        return pf.ProviderReply(out if isinstance(out, str) else json.dumps(out), "msg", *self.tokens)


def proposer(provider, clock, budget=Decimal("5")):
    def propose(pid, current, code_weight):
        provider.code_weight = code_weight
        snap = pp.snapshot(pid.split(":")[1], INPUTS | {"suggested_btc_weight": code_weight}, [("2026-01-01", 1.0)],
                           current, 0.0)
        return pp.propose(pid, snap, current, code_weight, provider, GRAPH, budget, clock)
    return propose


def run(closes, weights, provider, budget=Decimal("5")):
    """Drive an r2 run and a plain pathA-v1.2 book with the same Code targets."""
    daily, hourly = market(closes)
    days = list(daily.index)
    clock = Clock()
    propose = proposer(provider, clock, budget)
    pb, ref = ProposalBook(), PathABook()
    r, dn = stamps(days[0])
    clock.t = dn.to_pydatetime()
    pb.open(str(days[0].date()), weights[0], r, dn, ["open"], propose)
    ref.open(str(days[0].date()), weights[0], r, dn, ["open"])
    for k, day in enumerate(days[1:], start=1):
        sub_d, sub_h = daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)]
        pb.advance(sub_d, sub_h)
        ref.advance(sub_d, sub_h)
        r, dn = stamps(day)
        clock.t = dn.to_pydatetime()
        pb.decide(str(day.date()), weights[k], r, dn, ["x"], propose)
        ref.decide(str(day.date()), weights[k], r, dn, ["x"])
    return pb, ref, daily, hourly


def walk(seed, n=27):
    rng = np.random.default_rng(seed)
    return list(80000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))), list(rng.choice([0.0, 0.5, 1.0], n))


def paper_trades(book):
    return [(t["at"], t["side"], t["qty"]) for t in book.trades if t["book"] == "paper"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_ai_that_copies_code_reproduces_the_code_portfolio(seed):
    closes, weights = walk(seed)
    pb, ref, *_ = run(closes, weights, Scripted(lambda req, cw: answer(req, target_weight=cw)))
    assert paper_trades(pb.code) == paper_trades(ref) == paper_trades(pb.ai)
    assert [h["equity"] for h in pb.ai.history] == [h["equity"] for h in ref.history]
    assert pb.performance()["agree_with_code"] == pb.performance()["accepted"] == len(weights)


@pytest.mark.parametrize("make, category", [
    (lambda q: "{not json", "INVALID:INVALID_JSON"),
    (lambda q: answer(q, target_weight=0.7), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, target_weight=True), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, template_id="perp_leverage_v1"), "INVALID:UNKNOWN_TEMPLATE"),
    (lambda q: answer(q, evidence_refs=["news:invented"]), "INVALID:UNKNOWN_EVIDENCE"),
    (lambda q: answer(q, evidence_refs=["feature:suggested_btc_weight"]), "INVALID:UNKNOWN_EVIDENCE"),
    (lambda q: answer(q, evidence_refs=[f"feature:x{i}" for i in range(9)]), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, reason="x" * 501), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, reason_code="VETO"), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, side="SHORT"), "INVALID:INVALID_SCHEMA"),
    (lambda q: answer(q, proposal_id="p5r2:other:proposal"), "INVALID:MISMATCHED_IDS"),
    (lambda q: answer(q, policy_version="other"), "INVALID:MISMATCHED_IDS"),
    (lambda q: pf.ProviderReply(None, "r", 10, 0, error="REFUSAL"), "REFUSAL"),
    (lambda q: pf.ProviderReply(None, error="TIMEOUT"), "TIMEOUT"),
    (lambda q: pf.ProviderReply(None, error="PROVIDER_ERROR:APIConnectionError"), "PROVIDER_ERROR:APIConnectionError"),
])
def test_every_invalid_answer_keeps_the_weight_and_is_not_a_decision(make, category):
    closes, weights = walk(9, 6)
    good = {"n": 0}

    def first_ok_then_bad(req, cw):
        good["n"] += 1
        return answer(req, target_weight=0.5) if good["n"] == 1 else make(req)
    pb, *_ = run(closes, [1.0] * 6, Scripted(first_ok_then_bad))
    bad = pb.proposals[1:]
    assert all(not r["outcome"]["accepted"] and r["outcome"]["error_category"] == category for r in bad)
    assert all(r["outcome"]["target_weight"] == 0.5 and r["response"] is None for r in bad)
    assert all(h["weight_held_at_close"] == 0.5 for h in pb.ai.history)
    assert [e["kind"] for e in pb.ai.journal if e["kind"] not in ("trade", "alert")] == ["open"] + ["proposal_failed"] * 5
    p = pb.performance()
    assert p["accepted"] == 1 and p["failures"] == {category: 5} and p["ai_changes"] == 1


def test_late_answer_keeps_the_weight():
    clock = Clock()

    def slow(req, cw):
        clock.t = clock.t + pd.Timedelta(seconds=121).to_pytimedelta()
        return answer(req)
    snap = pp.snapshot("2026-01-01", INPUTS, [], 0.5, 0.0)
    rec = pp.propose("p5r2:2026-01-01:proposal", snap, 0.5, 1.0, Scripted(slow), GRAPH, Decimal(5), clock)
    assert not rec["outcome"]["accepted"] and rec["outcome"]["error_category"] == "LATE"
    assert rec["outcome"]["target_weight"] == 0.5 and rec["attempt"]["status"] == "TIMEOUT"


def test_prompt_never_contains_code_weight():
    closes, weights = walk(10, 10)
    prov = Scripted(lambda req, cw: answer(req, target_weight=0.5))
    pb, *_ = run(closes, weights, prov)
    assert prov.users and all("suggested_btc_weight" not in u for u in prov.users)
    snap = pp.snapshot("2026-01-01", INPUTS, [], 0.0, 0.0)
    assert not any("suggested" in k for k in snap["evidence"]) and snap["snapshot_id"].startswith("p5r2snap:")
    assert [r["code_weight"] for r in pb.proposals] == weights[:len(pb.proposals)]     # recorded beside, for the report


def test_halt_sells_and_is_absorbing_proposals_after_it_change_nothing():
    closes = [80000.0, 81000.0, 82000.0, 60000.0, 59000.0, 70000.0, 80000.0]
    pb, *_ = run(closes, [1.0] * 7, Scripted(lambda req, cw: answer(req, target_weight=1.0)))
    assert pb.ai.risk.halted
    assert [t["side"] for t in pb.ai.trades if t["book"] == "paper"] == ["BUY", "SELL"]
    assert all(h["weight_held_at_close"] == 0.0 for h in pb.ai.history[3:])
    after = [r for r in pb.proposals if r.get("ignored_halted")]
    assert after and all(r["outcome"]["accepted"] for r in after)
    assert "ignored_halted" in [e["kind"] for e in pb.ai.journal]


def test_random_proposer_is_deterministic_and_matches_the_change_count():
    closes, weights = walk(11)
    flip = Scripted(lambda req, cw: answer(req, target_weight=[0.0, 1.0, 0.5][int(req["proposal_id"][13:15]) % 3]))
    pb, _, daily, hourly = run(closes, weights, flip)
    n = pb.ai_changes()
    assert 0 < n <= len(pb.targets)
    days = [t["decided_on"] for t in pb.targets]
    for seed in range(20):
        w = random_weights(days, n, seed)
        assert w == random_weights(days, n, seed) and set(w) <= {0.0, 0.5, 1.0}
        assert sum(a != b for a, b in zip([0.0] + w[:-1], w, strict=True)) == n
    a = random_proposer(pb.targets, n, daily, hourly, 3, pb.endpoint)
    b = random_proposer(pb.targets, n, daily, hourly, 3, pb.endpoint)
    assert paper_trades(a) == paper_trades(b) and a.history == b.history
    assert a.last_day == pb.ai.last_day                              # a full path to the same close
    assert random_weights(days, n, 1) != random_weights(days, n, 2)


def test_cost_balances_lowers_economic_equity_only_and_cap_fails_closed():
    closes, weights = walk(12, 8)
    pb, ref, *_ = run(closes, weights, Scripted(lambda req, cw: answer(req, target_weight=cw), tokens=(5000, 2000)))
    assert pb.spent_usd() == Decimal("0.06") * len(pb.proposals)
    assert pb.ai.book.check() == [] and pb.ai.book.bal("operating_costs") == pb.spent_usd()
    last = pb.ai.history[-1]
    booked = sum((Decimal(r["cost_usd"]) for r in pb.proposals
                  if r["attempt"]["response_at"] < last["date"] + "T23:59:59.999999+00:00"), Decimal(0))
    assert Decimal(last["equity"]) - Decimal(last["economic_equity"]) == booked
    assert last["equity"] == ref.history[-1]["equity"]

    called = []
    capped, *_ = run(closes, weights, Scripted(lambda req, cw: called.append(1) or answer(req)), budget=Decimal("0.49"))
    assert called == [] and capped.spent_usd() == 0
    assert all(r["outcome"]["error_category"] == "BUDGET_CAP" and r["attempt"] is None for r in capped.proposals)
    assert all(h["weight_held_at_close"] == 0.0 for h in capped.ai.history)       # never opened into BTC


def test_other_model_is_refused():
    class P:
        model_id = "claude-sonnet-5-5"

        def ask(self, *a):
            raise AssertionError("must not be called")
    with pytest.raises(ValueError, match="locked"):
        pp.propose("p5r2:x:proposal", pp.snapshot("2026-01-01", {}, [], 0.0, 0.0), 0.0, 1.0, P(), GRAPH, Decimal(5), Clock())


def test_endpoint_and_state_round_trip():
    closes, weights = walk(13, 35)
    pb, *_ = run(closes, weights, Scripted(lambda req, cw: answer(req, target_weight=cw)))
    assert pb.endpoint == "2026-01-29" and pb.code.last_day == pb.ai.last_day == pb.endpoint
    assert max(r["proposal_id"] for r in pb.proposals) == "p5r2:2026-01-29:proposal"
    back = ProposalBook(json.loads(json.dumps(pb.to_dict())))
    assert back.performance() == pb.performance()
    with pytest.raises(ValueError):
        ProposalBook({**pb.to_dict(), "experiment_id": "other"})


def test_contract_enables_bounded_proposal_only_for_the_compiled_template():
    d = GRAPH.model_dump(mode="json")
    assert GRAPH.capability_stage == "BOUNDED_PROPOSAL" and GRAPH.lock_problems() == []
    with pytest.raises(ValidationError, match="no template compiler"):
        AgentGraphConfig.model_validate(d | {"proposal_template_id": "perp_leverage_v1"})
    with pytest.raises(ValidationError, match="proposal_template_id is set exactly"):
        AgentGraphConfig.model_validate(d | {"proposal_template_id": None})
    with pytest.raises(ValidationError, match="exactly one proposer"):
        AgentGraphConfig.model_validate(d | {"roles": d["roles"] + [d["roles"][0] | {"role_id": "second"}],
                                             "required_roles": ["proposer", "second"]})
    f = load_graph(pf.GRAPH_FILE).model_dump(mode="json")
    with pytest.raises(ValidationError, match="proposer roles are only for BOUNDED_PROPOSAL"):
        AgentGraphConfig.model_validate(f | {"roles": [f["roles"][0] | {"kind": "proposer"}]})
    with pytest.raises(ValidationError, match="proposal_template_id is set exactly"):
        AgentGraphConfig.model_validate(f | {"proposal_template_id": "pathA_spot_weight_v1"})


def test_r1_graph_hash_is_unchanged_by_the_new_field():
    assert pf.load_filter_graph().graph_hash() == "99f28f701e3010cb889cf43de98b0a42e97434aa3a44d6e1a8c0251aed10e00b"


def test_trading_mirror_copies_both_r2_arms(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    closes, weights = walk(14, 8)
    pb, *_ = run(closes, weights, Scripted(lambda req, cw: answer(req, target_weight=cw), tokens=(5000, 2000)))
    (tmp_path / "pathA-p5-r2").mkdir()
    (tmp_path / "pathA-p5-r2" / "state.json").write_text(json.dumps(pb.to_dict()))
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, str(root / "scripts/trading_sync_sql.py"), "--root", str(tmp_path),
                        "--commit", "pathA-p5-r2=abc1234"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for label in ("pathA-p5-r2/code", "pathA-p5-r2/ai", "pathA-p5-r2/buy_and_hold"):
        assert f"'{label}'" in r.stdout
    assert "'pathA-p5-r2/ai:llm:p5r2:" in r.stdout
    s = subprocess.run([sys.executable, str(root / "scripts/supabase_sync_sql.py"), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert s.returncode == 0 and "pathA-p5-r2 (proposal_book.v1) is not mirrored" in s.stderr
