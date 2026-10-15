"""Versioned decision policy over validated analysis output (§7.4, §7.9).

Every failure path ends in ABSTAIN with a reliability reason, never a credited
VETO. ALLOW is only permission to continue: final risk (§8–§14) can still reject.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading.analysis.validation import analysis_problems, filter_response_problems, synthesis_problems
from trading.contracts.analysis import (
    AgentAnalysis,
    AgentGraphConfig,
    AnalysisRequest,
    AttemptStatus,
    CapabilityStage,
    Disposition,
    FilterDecision,
    FilterResponse,
    PolicyDecision,
    ReasonCode,
    SynthesisResult,
)

POLICY_VERSION = "analysis_policy_v1.0"


@dataclass(frozen=True)
class Runtime:
    """Backend facts at decision time; never taken from model output."""
    now: datetime
    effective_deadline: datetime
    budget_permitted: bool
    evidence: frozenset[str]
    missing_required_inputs: frozenset[str] = frozenset()


def effective_deadline(req: AnalysisRequest, graph: AgentGraphConfig, run_start: datetime) -> datetime:
    """min(proposal valid_until, run_start + total timeout); AI never extends a proposal's TTL (§7.8)."""
    if graph.total_timeout_ms is None:
        raise ValueError("graph has no total_timeout_ms; lock it before starting runs")
    return min(req.valid_until, run_start + timedelta(milliseconds=graph.total_timeout_ms))


def _decision(req: AnalysisRequest, graph: AgentGraphConfig, rt: Runtime, decision: FilterDecision,
              reason: ReasonCode, refs: tuple[str, ...] = (), valid: bool = True,
              missing: tuple[str, ...] = ()) -> PolicyDecision:
    return PolicyDecision(run_id=req.run_id, proposal_id=req.proposal_id, decision=decision, reason_code=reason,
                          evidence_refs=refs, graph_hash=graph.graph_hash(), policy_version=POLICY_VERSION,
                          validation_state="VALID" if valid else "INVALID", validated_at=rt.now,
                          partial_context=bool(missing), missing_roles=missing)


def _abstain(req: AnalysisRequest, graph: AgentGraphConfig, rt: Runtime, reason: ReasonCode,
             valid: bool = True) -> PolicyDecision:
    return _decision(req, graph, rt, FilterDecision.ABSTAIN, reason, valid=valid)


def _common_gates(req: AnalysisRequest, graph: AgentGraphConfig, rt: Runtime,
                  stage: CapabilityStage) -> PolicyDecision | None:
    if graph.capability_stage is not stage:
        raise ValueError(f"graph stage {graph.capability_stage} cannot use the {stage} policy")
    if req.graph_hash != graph.graph_hash():
        raise ValueError("request was built for a different graph")
    if rt.now >= rt.effective_deadline:
        return _abstain(req, graph, rt, ReasonCode.EXPIRED_DECISION)
    if not rt.budget_permitted:
        return _abstain(req, graph, rt, ReasonCode.BUDGET_UNAVAILABLE)
    return None


def decide_filter_only(req: AnalysisRequest, graph: AgentGraphConfig, status: AttemptStatus,
                       resp: FilterResponse | None, rt: Runtime) -> PolicyDecision:
    """v1 single filter adapter: one provider call, no fallback model, no retries (§7.4)."""
    if early := _common_gates(req, graph, rt, CapabilityStage.FILTER_ONLY):
        return early
    if status is not AttemptStatus.SUCCESS or resp is None:
        reason = {AttemptStatus.INVALID: ReasonCode.INVALID_RESPONSE,
                  AttemptStatus.TIMEOUT: ReasonCode.EXPIRED_DECISION}.get(status, ReasonCode.PROVIDER_UNAVAILABLE)
        return _abstain(req, graph, rt, reason, valid=status is not AttemptStatus.INVALID)
    if filter_response_problems(req, resp, rt.evidence):
        return _abstain(req, graph, rt, ReasonCode.INVALID_RESPONSE, valid=False)
    if rt.missing_required_inputs and resp.decision is FilterDecision.ALLOW:
        return _abstain(req, graph, rt, ReasonCode.UNRESOLVED_CONTEXT)
    return _decision(req, graph, rt, resp.decision, resp.reason_code, resp.evidence_refs)


def decide_multi_filter(req: AnalysisRequest, graph: AgentGraphConfig,
                        reports: Mapping[str, tuple[AttemptStatus, AgentAnalysis | None]],
                        synthesis: SynthesisResult | None, rt: Runtime) -> PolicyDecision:
    """§7.9 decision logic. `reports` is the sealed set keyed by role id; late reports are not added."""
    if early := _common_gates(req, graph, rt, CapabilityStage.MULTI_ANALYST_FILTER):
        return early
    if set(reports) - set(graph.required_roles) - set(graph.optional_roles):
        return _abstain(req, graph, rt, ReasonCode.INVALID_REPORT_SET, valid=False)

    valid: dict[str, AgentAnalysis] = {}
    for role_id, (status, report) in reports.items():
        if status is AttemptStatus.SUCCESS and report is not None:
            if report.role_id != role_id or analysis_problems(req, graph, report, rt.evidence):
                return _abstain(req, graph, rt, ReasonCode.INVALID_REPORT_SET, valid=False)
            valid[role_id] = report

    if not set(graph.required_roles) <= valid.keys():
        return _abstain(req, graph, rt, ReasonCode.REQUIRED_ROLE_UNAVAILABLE)
    missing_optional = tuple(sorted(set(graph.optional_roles) - valid.keys()))
    if missing_optional and not graph.allow_partial_optional:
        return _abstain(req, graph, rt, ReasonCode.OPTIONAL_ROLE_REQUIRED_BY_POLICY)
    if synthesis is None or synthesis_problems(req, valid, synthesis, rt.evidence, rt.missing_required_inputs):
        return _abstain(req, graph, rt, ReasonCode.INVALID_SYNTHESIS, valid=False)
    if synthesis.unresolved_conflicts or rt.missing_required_inputs:
        return _abstain(req, graph, rt, ReasonCode.UNRESOLVED_CONTEXT)
    if synthesis.suggested_disposition is Disposition.OPPOSE:
        return _decision(req, graph, rt, FilterDecision.VETO, ReasonCode.CONTEXT_CONFLICT,
                         synthesis.evidence_refs, missing=missing_optional)
    if synthesis.suggested_disposition is Disposition.SUPPORT:
        return _decision(req, graph, rt, FilterDecision.ALLOW, ReasonCode.SUPPORTED_CONTEXT,
                         synthesis.evidence_refs, missing=missing_optional)
    return _decision(req, graph, rt, FilterDecision.ABSTAIN, ReasonCode.UNCERTAIN_CONTEXT, missing=missing_optional)
