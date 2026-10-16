"""Phase 5 path A paired run: acceptance tests declared in docs/phase-5-paired-plan.md."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.analysis import pathA_filter as pf
from trading.shadow.paired_book import PairedBook, random_veto, uniform
from trading.shadow.rebalance_book import PathABook

D0 = pd.Timestamp("2026-01-01", tz="UTC")
GRAPH = pf.load_filter_graph()


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


def asker(provider, clock, budget=Decimal("5")):
    snap = pf.snapshot("2026-01-01", {"close_perp": 1.0, "funding_window": []}, [("2026-01-01", 1.0)], 0.0, 0.0)

    def ask(kind, pid, current, target):
        return pf.ask(kind, pid, snap, current, target, provider, GRAPH, budget, clock)
    return ask


def run(closes, weights, provider, upto=None, diagnostic=True):
    """Drive a paired run and a plain pathA-v1.2 book with the same Code targets."""
    daily, hourly = market(closes)
    days = list(daily.index)[:upto]
    clock = Clock()
    ask = asker(provider, clock)
    pb, ref = PairedBook(), PathABook()
    r, dn = stamps(days[0])
    clock.t = dn.to_pydatetime()
    pb.open(str(days[0].date()), weights[0], r, dn, ["open"], ask)
    ref.open(str(days[0].date()), weights[0], r, dn, ["open"])
    for k, day in enumerate(days[1:], start=1):
        sub_d, sub_h = daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)]
        pb.advance(sub_d, sub_h)
        ref.advance(sub_d, sub_h)
        r, dn = stamps(day)
        clock.t = dn.to_pydatetime()
        pb.decide(str(day.date()), weights[k], r, dn, ["x"], ask, diagnostic=diagnostic)
        ref.decide(str(day.date()), weights[k], r, dn, ["x"])
    return pb, ref, daily, hourly


def walk(seed, n=27):
    rng = np.random.default_rng(seed)
    closes = list(80000 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    weights = list(rng.choice([0.0, 0.5, 1.0], n))
    return closes, weights


def paper_trades(book):
    return [(t["at"], t["side"], t["qty"]) for t in book.trades if t["book"] == "paper"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_code_arm_is_the_v12_book_and_always_allow_ai_matches_it(seed):
    closes, weights = walk(seed)
    pb, ref, *_ = run(closes, weights, pf.MockProvider("ALLOW"))
    assert paper_trades(pb.code) == paper_trades(ref)
    assert [h["equity"] for h in pb.code.history] == [h["equity"] for h in ref.history]
    assert paper_trades(pb.ai) == paper_trades(ref)
    assert pb.ai.history[-1]["equity"] == ref.history[-1]["equity"]


def test_always_veto_never_increases_and_decreases_pass():
    closes = [80000.0 + 100 * i for i in range(10)]
    weights = [1.0, 1.0, 0.5, 0.5, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0]
    first = {"n": 0}

    def allow_first_then_veto(req):
        first["n"] += 1
        d = "ALLOW" if first["n"] == 1 else "VETO"
        return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "decision": d,
                "reason_code": "SUPPORTED_CONTEXT" if d == "ALLOW" else "CONTEXT_CONFLICT", "reason": "m",
                "evidence_refs": [], "policy_version": pf.FILTER_POLICY}
    pb, *_ = run(closes, weights, pf.MockProvider(allow_first_then_veto), diagnostic=False)
    held = [h["weight_held_at_close"] for h in pb.ai.history]
    assert held[0] == 1.0 and 0.5 in held and held[-1] == 0.0       # opened (ALLOW), then only went down
    assert [e["kind"] for e in pb.ai.journal].count("filtered") == 4   # each day the target stays above it is asked again
    assert all(r["decision"]["decision"] in ("ALLOW", "VETO") for r in pb.filter_log)

    pb2, *_ = run(closes, weights, pf.MockProvider("VETO"), diagnostic=False)
    assert all(h["weight_held_at_close"] == 0.0 for h in pb2.ai.history)
    assert paper_trades(pb2.ai) == []


def test_halt_sells_pass_through_a_vetoing_ai():
    closes = [80000.0, 81000.0, 82000.0, 60000.0, 59000.0, 58000.0]
    weights = [1.0] * 6
    first = {"n": 0}

    def allow_once(req):
        first["n"] += 1
        return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"],
                "decision": "ALLOW" if first["n"] == 1 else "VETO", "reason_code": "SUPPORTED_CONTEXT", "reason": "m",
                "evidence_refs": [], "policy_version": pf.FILTER_POLICY}
    pb, *_ = run(closes, weights, pf.MockProvider(allow_once))
    assert pb.ai.risk.halted and pb.code.risk.halted
    assert [t["side"] for t in pb.ai.trades if t["book"] == "paper"] == ["BUY", "SELL"]
    assert pb.ai.history[-1]["weight_held_at_close"] == 0.0


def _record(reply_or_fn, clock=None, budget=Decimal("5")):
    class P:
        model_id = "claude-opus-5-5"

        def ask(self, system, user, schema, max_tokens, timeout_s):
            return reply_or_fn(json.loads(user.split("REQUEST:", 1)[1].split("\nSNAPSHOT:", 1)[0])) \
                if callable(reply_or_fn) else reply_or_fn
    clock = clock or Clock()
    snap = pf.snapshot("2026-01-01", {"close_perp": 1.0, "funding_window": []}, [("2026-01-01", 1.0)], 0.0, 0.0)
    return pf.ask("filter", "p5:2026-01-01:filter", snap, 0.0, 1.0, P(), GRAPH, budget, clock)


def _ok(req, **over):
    return json.dumps({"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "decision": "VETO",
                       "reason_code": "CONTEXT_CONFLICT", "reason": "r", "evidence_refs": ["feature:close_perp"],
                       "policy_version": pf.FILTER_POLICY} | over)


@pytest.mark.parametrize("make, reason", [
    (lambda q: pf.ProviderReply("{not json", "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, reason_code="BUDGET_UNAVAILABLE"), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, reason_code="MADE_UP"), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, evidence_refs=["news:invented"]), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, extra="x"), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, proposal_id="p5:other"), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(_ok(q, policy_version="other"), "r", 10, 10), "INVALID_RESPONSE"),
    (lambda q: pf.ProviderReply(None, "r", 10, 0, error="REFUSAL"), "PROVIDER_UNAVAILABLE"),
    (lambda q: pf.ProviderReply(None, error="TIMEOUT"), "EXPIRED_DECISION"),
])
def test_failures_abstain_with_a_system_reason_never_a_veto(make, reason):
    rec = _record(make)
    assert rec["decision"]["decision"] == "ABSTAIN" and rec["decision"]["reason_code"] == reason


def test_valid_veto_is_a_market_decision_and_cost_is_recorded():
    rec = _record(lambda q: pf.ProviderReply(_ok(q), "msg_1", 5000, 2000))
    assert rec["decision"]["decision"] == "VETO" and rec["decision"]["reason_code"] == "CONTEXT_CONFLICT"
    assert Decimal(rec["cost_usd"]) == Decimal("0.06")               # 5k x $4/M + 2k x $20/M
    assert rec["attempt"]["prompt_hash"] == pf.prompt_hash() and rec["attempt"]["model_id"] == "claude-opus-5-5"


def test_late_answer_is_expired():
    clock = Clock()

    def slow(q):
        clock.t = clock.t + pd.Timedelta(seconds=121).to_pytimedelta()
        return pf.ProviderReply(_ok(q, decision="ALLOW", reason_code="SUPPORTED_CONTEXT"), "r", 10, 10)
    rec = _record(slow, clock)
    assert rec["decision"]["decision"] == "ABSTAIN" and rec["decision"]["reason_code"] == "EXPIRED_DECISION"


def test_budget_cap_fails_closed_without_a_call():
    called = []
    rec = _record(lambda q: called.append(q) or pf.ProviderReply(_ok(q), "r", 1, 1), budget=Decimal("0.49"))
    assert called == [] and rec["attempt"] is None
    assert rec["decision"]["reason_code"] == "BUDGET_UNAVAILABLE" and rec["cost_usd"] == "0"


def test_other_model_is_refused():
    class P:
        model_id = "claude-sonnet-5-5"

        def ask(self, *a):
            raise AssertionError("must not be called")
    snap = pf.snapshot("2026-01-01", {}, [], 0.0, 0.0)
    with pytest.raises(ValueError, match="locked"):
        pf.ask("filter", "p5:x:filter", snap, 0.0, 1.0, P(), GRAPH, Decimal(5), Clock())


def test_ai_cost_lowers_economic_not_trading_equity_and_entries_balance():
    class Paid:
        model_id = "claude-opus-5-5"

        def ask(self, system, user, schema, max_tokens, timeout_s):
            q = json.loads(user.split("REQUEST:", 1)[1].split("\nSNAPSHOT:", 1)[0])
            return pf.ProviderReply(_ok(q, decision="ALLOW", reason_code="SUPPORTED_CONTEXT", evidence_refs=[]),
                                    "msg", 5000, 2000)
    closes, weights = walk(4, 8)
    pb, ref, *_ = run(closes, weights, Paid())
    calls = len(pb.filter_log) + len(pb.diagnostics)
    assert calls > 0 and pb.spent_usd() == Decimal("0.06") * calls
    assert pb.ai.book.check() == [] and pb.ai.book.bal("operating_costs") == pb.spent_usd()
    last = pb.ai.history[-1]
    booked_by_close = sum((Decimal(r["cost_usd"]) for r in pb.filter_log + pb.diagnostics
                           if r["attempt"]["response_at"] < last["date"] + "T23:59:59.999999+00:00"), Decimal(0))
    assert Decimal(last["equity"]) - Decimal(last["economic_equity"]) == booked_by_close
    assert last["equity"] == ref.history[-1]["equity"]


def test_diagnostic_answers_change_nothing():
    def veto_diagnostics(req):
        d = "VETO" if req["proposal_id"].endswith(":diag") else "ALLOW"
        return {"proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "decision": d,
                "reason_code": "CONTEXT_CONFLICT" if d == "VETO" else "SUPPORTED_CONTEXT", "reason": "m",
                "evidence_refs": [], "policy_version": pf.FILTER_POLICY}
    closes, weights = walk(5)
    pb, ref, *_ = run(closes, weights, pf.MockProvider(veto_diagnostics))
    assert pb.diagnostics and all(r["decision"]["decision"] == "VETO" for r in pb.diagnostics)
    assert paper_trades(pb.ai) == paper_trades(ref)


def test_endpoint_stops_decisions_and_marks():
    closes, weights = walk(6, 35)
    pb, *_ = run(closes, weights, pf.MockProvider("ALLOW"))
    assert pb.endpoint == "2026-01-29"
    assert pb.code.last_day == pb.ai.last_day == pb.endpoint
    assert max(t["decided_on"] for t in pb.targets) == pb.endpoint
    assert all(r["proposal_id"] < "p5:2026-01-29:diag" for r in pb.diagnostics)


def test_state_round_trip():
    closes, weights = walk(7, 10)
    pb, *_ = run(closes, weights, pf.MockProvider("ALLOW"))
    back = PairedBook(json.loads(json.dumps(pb.to_dict())))
    assert back.performance() == pb.performance()
    with pytest.raises(ValueError):
        PairedBook({**pb.to_dict(), "experiment_id": "other"})


def test_random_veto_is_deterministic_and_bounded_by_p():
    closes, weights = walk(8)
    pb, ref, daily, hourly = run(closes, weights, pf.MockProvider("ALLOW"))
    a = random_veto(pb.targets, daily, hourly, 0.5, 3, pb.endpoint)
    b = random_veto(pb.targets, daily, hourly, 0.5, 3, pb.endpoint)
    assert paper_trades(a) == paper_trades(b)
    assert paper_trades(random_veto(pb.targets, daily, hourly, 0.0, 1, pb.endpoint)) == paper_trades(ref)
    never = random_veto(pb.targets, daily, hourly, 1.0, 1, pb.endpoint)
    assert all(t["side"] == "SELL" for t in never.trades if t["book"] == "paper") or not never.trades
    assert 0 <= uniform(1, "x") < 1 and uniform(1, "x") == uniform(1, "x") != uniform(2, "x")
