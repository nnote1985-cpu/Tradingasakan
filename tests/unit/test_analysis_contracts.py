"""Common analysis contracts, graph config and decision policy (BUILDPLAN v4.2 §7, §26.1).

Uses mock graphs and hand-written reports only; no model is called.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading.analysis.graphs import CONFIG_DIR, load_all, load_graph
from trading.analysis.policy import Runtime, decide_filter_only, decide_multi_filter, effective_deadline
from trading.contracts.analysis import (
    AgentAnalysis,
    AgentGraphConfig,
    AnalysisRequest,
    AnalysisRun,
    AttemptRecord,
    AttemptStatus,
    FilterDecision,
    FilterResponse,
    ReasonCode,
    RunState,
    SynthesisResult,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "analysis"
T0 = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
H = "a" * 64
EVIDENCE = frozenset({"feature:trend_4h", "feature:breakout_20", "feature:distance_atr", "news:example-001",
                      "derivatives:funding_7d"})


@pytest.fixture(scope="module")
def mock_filter() -> AgentGraphConfig:
    return load_graph(FIX / "mock_filter.yaml")


@pytest.fixture(scope="module")
def mock_multi() -> AgentGraphConfig:
    return load_graph(FIX / "mock_multi.yaml")


def request(graph: AgentGraphConfig, **kw) -> AnalysisRequest:
    base = dict(request_id="req-1", run_id="run-1", experiment_id="exp-1", arm_id="ai", proposal_id="prop-1",
                snapshot_id="snap-1", context_manifest_hash=H, as_of=T0, valid_until=T0 + timedelta(minutes=5),
                graph_hash=graph.graph_hash(), task="Filter the LONG proposal against the frozen context.")
    return AnalysisRequest(**(base | kw))


def runtime(**kw) -> Runtime:
    base = dict(now=T0 + timedelta(seconds=10), effective_deadline=T0 + timedelta(seconds=30),
                budget_permitted=True, evidence=EVIDENCE)
    return Runtime(**(base | kw))


def report(role: str, assessment: str = "SUPPORTS", refs=("feature:trend_4h",), **kw) -> AgentAnalysis:
    base = dict(run_id="run-1", role_id=role, snapshot_id="snap-1", assessment=assessment,
                findings=[{"claim_id": f"{role}-1", "kind": "OBSERVATION", "summary": "สรุปจาก feature ที่ให้",
                           "evidence_refs": list(refs)}],
                uncertainty="ไม่มีหลักฐานเพียงพอระบุโอกาสชนะเป็นเปอร์เซ็นต์")
    return AgentAnalysis.model_validate(base | kw)


def synth(disposition: str, reports=("regime", "setup"), conflicts=(), **kw) -> SynthesisResult:
    base = dict(run_id="run-1", cited_report_ids=list(reports), cited_claim_ids=[f"{r}-1" for r in reports],
                evidence_refs=["feature:trend_4h"], thesis="รวมหลักฐานจากรายงานที่ผ่านการตรวจ",
                unresolved_conflicts=list(conflicts), suggested_disposition=disposition)
    return SynthesisResult.model_validate(base | kw)


OK = AttemptStatus.SUCCESS


# --- graph config ------------------------------------------------------------

def test_repo_graph_configs_are_valid_drafts_that_cannot_be_enabled():
    graphs = load_all(CONFIG_DIR)
    assert set(graphs) == {"v1_filter", "multi_filter_draft_1", "pathA_p5_filter", "pathA_p5_r2_proposer"}
    for name in ("pathA_p5_filter",                 # the Phase 5 run's filter, locked by docs/phase-5-paired-plan.md
                 "pathA_p5_r2_proposer"):           # the r2 proposer, locked by docs/phase-5-r2-plan.md
        locked = graphs.pop(name)
        assert locked.enabled and locked.lock_problems() == [] and locked.tools == ()
    for g in graphs.values():
        assert not g.enabled
        assert g.lock_problems(), "drafts keep nulls until provider discovery and experiment lock"
        with pytest.raises(ValidationError, match="not lockable"):
            AgentGraphConfig.model_validate(g.model_dump() | {"enabled": True})


def test_mock_graphs_are_lockable(mock_filter, mock_multi):
    assert mock_filter.lock_problems() == [] and mock_multi.lock_problems() == []


def _multi(mock_multi, **changes):
    return mock_multi.model_dump(mode="json") | changes


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d | {"required_roles": ["regime", "setup", "news"]}, "both required and optional"),
    (lambda d: d | {"optional_roles": ["news"]}, "must equal the enabled analyst"),
    (lambda d: d | {"tools": ["get_features"]}, "server allowlist"),
    (lambda d: d | {"roles": d["roles"] + [d["roles"][0]]}, "unique"),
    (lambda d: d | {"roles": [r | {"depends_on": ["regime", "setup", "news"]} if r["role_id"] == "synthesis" else r
                              for r in d["roles"]]}, "synthesis must depend on every analyst"),
    (lambda d: d | {"roles": [r | {"depends_on": ["setup"]} if r["role_id"] == "regime" else r
                              for r in d["roles"]]}, "independent in the first round"),
    (lambda d: d | {"synthesis_required": False}, "synthesis"),
    (lambda d: d | {"max_attempts_per_role": 2}, "max_attempts_per_role"),
    (lambda d: d | {"surprise": 1}, "Extra inputs"),
])
def test_structural_rules(mock_multi, mutate, message):
    with pytest.raises(ValidationError, match=message):
        AgentGraphConfig.model_validate(mutate(mock_multi.model_dump(mode="json")))


def test_dependency_cycle_is_rejected(mock_multi):
    d = mock_multi.model_dump(mode="json")
    d["capability_stage"] = "BOUNDED_PROPOSAL"
    d["enabled"] = False
    d["roles"] = [r | {"depends_on": ["synthesis"]} if r["role_id"] == "regime" else r for r in d["roles"]]
    with pytest.raises(ValidationError, match="cycle"):
        AgentGraphConfig.model_validate(d)


@pytest.mark.parametrize("changes, message", [
    ({"max_run_cost_usd": "9.00"}, "exceeds daily_cost_cap_usd"),
    ({"analyst_cutoff_ms": 50000}, "exceeds total_timeout_ms"),
    ({"analyst_cutoff_ms": 10000}, "exceeds the analyst cutoff"),
    ({"total_timeout_ms": 30000}, "does not fit after the analyst cutoff"),
])
def test_budgets_and_deadlines_must_be_consistent_to_lock(mock_multi, changes, message):
    with pytest.raises(ValidationError, match=message):
        AgentGraphConfig.model_validate(_multi(mock_multi, **changes))


def test_float_amounts_are_refused(mock_filter):
    with pytest.raises(ValidationError):
        AgentGraphConfig.model_validate(mock_filter.model_dump(mode="json") | {"max_run_cost_usd": 0.05})


def test_filter_only_cannot_carry_synthesis(mock_filter):
    d = mock_filter.model_dump(mode="json") | {"synthesis_required": True}
    with pytest.raises(ValidationError, match="FILTER_ONLY"):
        AgentGraphConfig.model_validate(d)


def test_graph_hash_pins_every_field(mock_filter):
    other = AgentGraphConfig.model_validate(mock_filter.model_dump(mode="json") | {"max_concurrency": 2})
    assert other.graph_hash() != mock_filter.graph_hash()
    assert load_graph(FIX / "mock_filter.yaml").graph_hash() == mock_filter.graph_hash()


# --- request / response contracts -----------------------------------------------

def test_filter_response_matches_spec_example():
    r = FilterResponse.model_validate({
        "proposal_id": "example-proposal-001", "snapshot_id": "example-snapshot-001", "decision": "VETO",
        "reason_code": "CONTEXT_CONFLICT", "reason": "บริบทในข่าวขัดแย้งกับสมมติฐาน continuation",
        "evidence_refs": ["news:example-001", "feature:trend_4h"], "policy_version": "ai_filter_v1.0"})
    assert r.decision is FilterDecision.VETO


@pytest.mark.parametrize("changes", [
    {"confidence": 0.9},                                   # extra field
    {"decision": "STRONG_BUY"},
    {"reason_code": "EXPIRED_DECISION"},                   # backend-only reason
    {"reason": "x" * 501},
    {"evidence_refs": [f"feature:f{i}" for i in range(9)]},
    {"evidence_refs": ["not a ref"]},
])
def test_filter_response_rejects_out_of_contract_output(changes):
    base = {"proposal_id": "prop-1", "snapshot_id": "snap-1", "decision": "ALLOW", "reason_code": "SUPPORTED_CONTEXT",
            "reason": "ok", "evidence_refs": [], "policy_version": "ai_filter_v1.0"}
    with pytest.raises(ValidationError):
        FilterResponse.model_validate(base | changes)


def test_request_times_must_be_aware_and_ordered(mock_filter):
    with pytest.raises(ValidationError):
        request(mock_filter, as_of=datetime(2026, 10, 15, 8, 0))
    with pytest.raises(ValidationError):
        request(mock_filter, valid_until=T0)


def test_agent_analysis_limits():
    with pytest.raises(ValidationError):
        report("setup", findings=[{"claim_id": f"c{i}", "kind": "OBSERVATION", "summary": "s"} for i in range(9)])
    with pytest.raises(ValidationError, match="unique"):
        report("setup", findings=[{"claim_id": "c", "kind": "OBSERVATION", "summary": "s"}] * 2)


def test_unknown_attempt_keeps_cost_reservation():
    base = dict(attempt_id="att-1", run_id="run-1", role_id="filter", input_hash=H, request_at=T0)
    with pytest.raises(ValidationError, match="reservation"):
        AttemptRecord(status="UNKNOWN", cost_usd="0", **base)
    with pytest.raises(ValidationError, match="reservation"):
        AttemptRecord(status="UNKNOWN", **base)
    assert AttemptRecord(status="UNKNOWN", reserved_cost_usd="0.05", **base).cost_usd is None
    with pytest.raises(ValidationError, match="SUCCESS"):
        AttemptRecord(status="SUCCESS", **base)


def test_run_state_machine():
    run = AnalysisRun(run_id="run-1", graph_hash=H, request_id="req-1", started_at=T0, effective_deadline=T0)
    run = run.transition(RunState.RUNNING).transition(RunState.ANALYSTS_SEALED).transition(RunState.SYNTHESIZING)
    done = run.transition(RunState.FINALIZED)
    assert (done.state, done.revision) == (RunState.FINALIZED, 4)
    with pytest.raises(ValueError):
        done.transition(RunState.RUNNING)
    with pytest.raises(ValueError):
        AnalysisRun(run_id="r", graph_hash=H, request_id="q", started_at=T0, effective_deadline=T0).transition(
            RunState.SYNTHESIZING)


def test_effective_deadline_never_extends_proposal_ttl(mock_filter):
    req = request(mock_filter, valid_until=T0 + timedelta(seconds=10))
    assert effective_deadline(req, mock_filter, T0) == T0 + timedelta(seconds=10)
    req = request(mock_filter)
    assert effective_deadline(req, mock_filter, T0) == T0 + timedelta(milliseconds=30000)


# --- v1 filter adapter -----------------------------------------------------------

def filter_resp(**kw) -> FilterResponse:
    base = dict(proposal_id="prop-1", snapshot_id="snap-1", decision="ALLOW", reason_code="SUPPORTED_CONTEXT",
                reason="บริบทสนับสนุน", evidence_refs=["feature:trend_4h"], policy_version="ai_filter_v1.0")
    return FilterResponse.model_validate(base | kw)


def test_filter_passes_valid_decisions_through(mock_filter):
    req = request(mock_filter)
    allow = decide_filter_only(req, mock_filter, OK, filter_resp(), runtime())
    veto = decide_filter_only(req, mock_filter, OK, filter_resp(decision="VETO", reason_code="CONTEXT_CONFLICT"),
                              runtime())
    assert (allow.decision, veto.decision) == (FilterDecision.ALLOW, FilterDecision.VETO)
    assert allow.graph_hash == mock_filter.graph_hash() and allow.validation_state == "VALID"


@pytest.mark.parametrize("status, resp, rt, reason", [
    (OK, filter_resp(proposal_id="other"), runtime(), ReasonCode.INVALID_RESPONSE),
    (OK, filter_resp(evidence_refs=["news:not-in-snapshot"]), runtime(), ReasonCode.INVALID_RESPONSE),
    (AttemptStatus.TIMEOUT, None, runtime(), ReasonCode.EXPIRED_DECISION),
    (AttemptStatus.PROVIDER_ERROR, None, runtime(), ReasonCode.PROVIDER_UNAVAILABLE),
    (AttemptStatus.INVALID, None, runtime(), ReasonCode.INVALID_RESPONSE),
    (OK, filter_resp(), runtime(now=T0 + timedelta(seconds=31)), ReasonCode.EXPIRED_DECISION),
    (OK, filter_resp(), runtime(budget_permitted=False), ReasonCode.BUDGET_UNAVAILABLE),
    (OK, filter_resp(), runtime(missing_required_inputs=frozenset({"news"})), ReasonCode.UNRESOLVED_CONTEXT),
])
def test_filter_failures_abstain_and_are_not_vetoes(mock_filter, status, resp, rt, reason):
    d = decide_filter_only(request(mock_filter), mock_filter, status, resp, rt)
    assert (d.decision, d.reason_code) == (FilterDecision.ABSTAIN, reason)


def test_policy_refuses_request_for_another_graph(mock_filter, mock_multi):
    with pytest.raises(ValueError, match="different graph"):
        decide_filter_only(request(mock_filter, graph_hash="b" * 64), mock_filter, OK, filter_resp(), runtime())
    with pytest.raises(ValueError, match="cannot use"):
        decide_filter_only(request(mock_multi), mock_multi, OK, filter_resp(), runtime())


# --- mock multi-role worked scenarios (§7.9 table) -----------------------------------

def full(**overrides):
    reps = {r: (OK, report(r)) for r in ("regime", "setup", "news", "derivatives_liquidity")}
    return reps | overrides


def test_scenario_unresolved_conflict_abstains_without_vote(mock_multi):
    reps = full(derivatives_liquidity=(OK, report("derivatives_liquidity", "OPPOSES", refs=("derivatives:funding_7d",))))
    s = synth("SUPPORT", reports=list(reps), conflicts=["liquidity คัดค้าน trend"])
    d = decide_multi_filter(request(mock_multi), mock_multi, reps, s, runtime())
    assert (d.decision, d.reason_code) == (FilterDecision.ABSTAIN, ReasonCode.UNRESOLVED_CONTEXT)


def test_scenario_partial_optional_allows_with_flag(mock_multi):
    reps = full(news=(AttemptStatus.TIMEOUT, None))
    s = synth("SUPPORT", reports=["regime", "setup", "derivatives_liquidity"])
    d = decide_multi_filter(request(mock_multi), mock_multi, reps, s, runtime())
    assert (d.decision, d.partial_context, d.missing_roles) == (FilterDecision.ALLOW, True, ("news",))


def test_scenario_news_required_abstains(mock_multi):
    g = AgentGraphConfig.model_validate(mock_multi.model_dump(mode="json") | {
        "required_roles": ["regime", "setup", "news"], "optional_roles": ["derivatives_liquidity"]})
    reps = full(news=(AttemptStatus.TIMEOUT, None))
    d = decide_multi_filter(request(g), g, reps, synth("SUPPORT"), runtime())
    assert d.reason_code is ReasonCode.REQUIRED_ROLE_UNAVAILABLE and d.decision is FilterDecision.ABSTAIN


def test_scenario_partial_not_allowed_by_policy(mock_multi):
    g = AgentGraphConfig.model_validate(mock_multi.model_dump(mode="json") | {"allow_partial_optional": False})
    d = decide_multi_filter(request(g), g, full(news=(AttemptStatus.TIMEOUT, None)), synth("SUPPORT"), runtime())
    assert d.reason_code is ReasonCode.OPTIONAL_ROLE_REQUIRED_BY_POLICY


def test_scenario_oppose_vetoes(mock_multi):
    d = decide_multi_filter(request(mock_multi), mock_multi, full(), synth("OPPOSE"), runtime())
    assert (d.decision, d.reason_code) == (FilterDecision.VETO, ReasonCode.CONTEXT_CONFLICT)


def test_scenario_uncertain_and_late_and_budget(mock_multi):
    req = request(mock_multi)
    assert decide_multi_filter(req, mock_multi, full(), synth("UNCERTAIN"), runtime()).reason_code \
        is ReasonCode.UNCERTAIN_CONTEXT
    late = runtime(now=T0 + timedelta(seconds=40))
    assert decide_multi_filter(req, mock_multi, full(), synth("SUPPORT"), late).reason_code \
        is ReasonCode.EXPIRED_DECISION
    broke = runtime(budget_permitted=False)
    assert decide_multi_filter(req, mock_multi, full(), synth("SUPPORT"), broke).reason_code \
        is ReasonCode.BUDGET_UNAVAILABLE


@pytest.mark.parametrize("reps, s", [
    (full(setup=(OK, report("setup", refs=("news:invented",)))), synth("SUPPORT")),       # evidence not in snapshot
    (full(setup=(OK, report("regime"))), synth("SUPPORT")),                                # report under wrong role
    (full(setup=(OK, report("setup", run_id="run-other"))), synth("SUPPORT")),
    (full() | {"reviewer": (OK, report("regime"))}, synth("SUPPORT")),                    # role not in graph
])
def test_invalid_report_sets_abstain(mock_multi, reps, s):
    d = decide_multi_filter(request(mock_multi), mock_multi, reps, s, runtime())
    assert (d.decision, d.reason_code) == (FilterDecision.ABSTAIN, ReasonCode.INVALID_REPORT_SET)


@pytest.mark.parametrize("s", [
    None,
    synth("SUPPORT", reports=["regime", "setup", "ghost"]),
    synth("SUPPORT", cited_claim_ids=["made-up-1"]),
    synth("SUPPORT", evidence_refs=["news:invented"]),
])
def test_invalid_synthesis_abstains(mock_multi, s):
    d = decide_multi_filter(request(mock_multi), mock_multi, full(), s, runtime())
    assert d.reason_code is ReasonCode.INVALID_SYNTHESIS


def test_backend_missing_inputs_override_model(mock_multi):
    rt = runtime(missing_required_inputs=frozenset({"funding"}))
    d = decide_multi_filter(request(mock_multi), mock_multi, full(), synth("SUPPORT"), rt)
    assert d.reason_code is ReasonCode.INVALID_SYNTHESIS          # synthesis claimed nothing was missing
    d = decide_multi_filter(request(mock_multi), mock_multi, full(), synth("SUPPORT", missing_inputs=["funding"]), rt)
    assert d.reason_code is ReasonCode.UNRESOLVED_CONTEXT


def test_every_reason_code_is_classified_and_system_reasons_abstain(mock_filter):
    from trading.contracts.analysis import REASON_KIND, PolicyDecision, ReasonKind
    assert set(REASON_KIND) == set(ReasonCode)
    base = dict(run_id="run-1", proposal_id="prop-1", graph_hash=mock_filter.graph_hash(), policy_version="p1",
                validation_state="VALID", validated_at=T0)
    with pytest.raises(ValidationError, match="never a market VETO"):
        PolicyDecision(decision="VETO", reason_code="PROVIDER_UNAVAILABLE", **base)
    d = PolicyDecision(decision="ABSTAIN", reason_code="BUDGET_UNAVAILABLE", **base)
    assert d.reason_kind is ReasonKind.SYSTEM and d.reason_codes_version == "reason_codes.v1"
