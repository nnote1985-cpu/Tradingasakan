"""Common analysis contracts and agent graph config (BUILDPLAN v4.2 §7.2, §7.6–§7.9, §8.2).

Strict schemas: unknown fields are rejected, every model is immutable, and
expanding a contract means a new schema_version rather than looser parsing.
Fields the backend owns (attempt ids, timestamps, usage, cost) live in the
envelopes below and are never taken from model output.

No provider or order calls happen here; these are data shapes and the
structural checks that must pass before a graph can be locked or a run start.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trading.contracts.money import DecimalStr

MAX_REASON = 500
MAX_REFS = 8
MAX_FINDINGS = 8

# Server-side tool allowlist (§7.10). v1 has no decision-time tools.
SERVER_TOOL_ALLOWLIST: frozenset[str] = frozenset()


def _aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return v


Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
EvidenceRef = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
AwareDatetime = Annotated[datetime, AfterValidator(_aware)]
Refs = Annotated[tuple[EvidenceRef, ...], Field(max_length=MAX_REFS)]
Text = Annotated[str, Field(min_length=1, max_length=MAX_REASON)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- enums -----------------------------------------------------------------

class CapabilityStage(StrEnum):
    FILTER_ONLY = "FILTER_ONLY"
    MULTI_ANALYST_FILTER = "MULTI_ANALYST_FILTER"
    BOUNDED_PROPOSAL = "BOUNDED_PROPOSAL"


class FilterDecision(StrEnum):
    ALLOW = "ALLOW"
    VETO = "VETO"
    ABSTAIN = "ABSTAIN"


class ReasonCode(StrEnum):
    """Allowlist for decision reasons (§7.2, §7.4, §7.9). New codes need a new policy version."""
    SUPPORTED_CONTEXT = "SUPPORTED_CONTEXT"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    UNCERTAIN_CONTEXT = "UNCERTAIN_CONTEXT"
    UNRESOLVED_CONTEXT = "UNRESOLVED_CONTEXT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EXPIRED_DECISION = "EXPIRED_DECISION"
    BUDGET_UNAVAILABLE = "BUDGET_UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    INVALID_REPORT_SET = "INVALID_REPORT_SET"
    INVALID_SYNTHESIS = "INVALID_SYNTHESIS"
    REQUIRED_ROLE_UNAVAILABLE = "REQUIRED_ROLE_UNAVAILABLE"
    OPTIONAL_ROLE_REQUIRED_BY_POLICY = "OPTIONAL_ROLE_REQUIRED_BY_POLICY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    NOT_EVALUATED_PORTFOLIO_INELIGIBLE = "NOT_EVALUATED_PORTFOLIO_INELIGIBLE"


REASON_CODES_VERSION = "reason_codes.v1"


class ReasonKind(StrEnum):
    """Keeps market judgements apart from system failures in every report (§7.4)."""
    MARKET = "market"                      # a view about the market context, given by the model or synthesis
    SYSTEM = "system"                      # reliability: deadline, budget, invalid output, provider down
    ELIGIBILITY = "eligibility"            # not evaluated at all; never counted as a VETO


REASON_KIND: dict[ReasonCode, ReasonKind] = {
    ReasonCode.SUPPORTED_CONTEXT: ReasonKind.MARKET,
    ReasonCode.CONTEXT_CONFLICT: ReasonKind.MARKET,
    ReasonCode.UNCERTAIN_CONTEXT: ReasonKind.MARKET,
    ReasonCode.UNRESOLVED_CONTEXT: ReasonKind.MARKET,
    ReasonCode.INSUFFICIENT_EVIDENCE: ReasonKind.MARKET,
    ReasonCode.EXPIRED_DECISION: ReasonKind.SYSTEM,
    ReasonCode.BUDGET_UNAVAILABLE: ReasonKind.SYSTEM,
    ReasonCode.INVALID_RESPONSE: ReasonKind.SYSTEM,
    ReasonCode.INVALID_REPORT_SET: ReasonKind.SYSTEM,
    ReasonCode.INVALID_SYNTHESIS: ReasonKind.SYSTEM,
    ReasonCode.REQUIRED_ROLE_UNAVAILABLE: ReasonKind.SYSTEM,
    ReasonCode.OPTIONAL_ROLE_REQUIRED_BY_POLICY: ReasonKind.SYSTEM,
    ReasonCode.PROVIDER_UNAVAILABLE: ReasonKind.SYSTEM,
    ReasonCode.NOT_EVALUATED_PORTFOLIO_INELIGIBLE: ReasonKind.ELIGIBILITY,
}

# Reasons a model may give itself; the rest are assigned by the backend.
MODEL_REASON_CODES = frozenset({ReasonCode.SUPPORTED_CONTEXT, ReasonCode.CONTEXT_CONFLICT,
                                ReasonCode.UNCERTAIN_CONTEXT, ReasonCode.INSUFFICIENT_EVIDENCE})


class AttemptStatus(StrEnum):
    """Reliability outcome, kept apart from market opinion (§7.8 step 4)."""
    SUCCESS = "SUCCESS"
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    CANCELLED = "CANCELLED"
    SKIPPED_MISSING_INPUT = "SKIPPED_MISSING_INPUT"
    UNKNOWN = "UNKNOWN"                    # dispatched, outcome not known after a crash


class RunState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    ANALYSTS_SEALED = "ANALYSTS_SEALED"
    SYNTHESIZING = "SYNTHESIZING"
    FINALIZED = "FINALIZED"
    ABSTAINED = "ABSTAINED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = frozenset({RunState.FINALIZED, RunState.ABSTAINED, RunState.EXPIRED, RunState.CANCELLED})
_STOP = frozenset({RunState.ABSTAINED, RunState.EXPIRED, RunState.CANCELLED})
RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.RUNNING}) | _STOP,
    RunState.RUNNING: frozenset({RunState.ANALYSTS_SEALED, RunState.FINALIZED}) | _STOP,   # FILTER_ONLY skips sealing
    RunState.ANALYSTS_SEALED: frozenset({RunState.SYNTHESIZING}) | _STOP,
    RunState.SYNTHESIZING: frozenset({RunState.FINALIZED}) | _STOP,
    **{s: frozenset() for s in TERMINAL_STATES},
}


class Assessment(StrEnum):
    SUPPORTS = "SUPPORTS"
    MIXED = "MIXED"
    OPPOSES = "OPPOSES"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FindingKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"


class Disposition(StrEnum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    UNCERTAIN = "UNCERTAIN"


class RoleKind(StrEnum):
    FILTER = "filter"                      # v1 single filter adapter
    ANALYST = "analyst"
    SYNTHESIS = "synthesis"
    REVIEWER = "reviewer"


# --- graph config (§7.6, §7.7, §7.9) ----------------------------------------

class AgentRole(_Strict):
    role_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    kind: RoleKind
    depends_on: tuple[str, ...] = ()
    model_id: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    timeout_ms: Annotated[int, Field(gt=0)] | None = None
    max_output_tokens: Annotated[int, Field(gt=0)] | None = None
    input_scopes: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()


class AgentGraphConfig(_Strict):
    """A pinned analysis graph. Structural rules always hold; lock rules hold once enabled."""
    schema_version: Literal["agent_graph.v1"] = "agent_graph.v1"
    graph_version: Id
    enabled: bool = False
    capability_stage: CapabilityStage
    roles: tuple[AgentRole, ...] = Field(min_length=1)
    required_roles: tuple[str, ...] = ()
    optional_roles: tuple[str, ...] = ()
    synthesis_required: bool = False
    reviewer_enabled: bool = False
    allow_partial_optional: bool = False
    required_failure: Literal["ABSTAIN"] = "ABSTAIN"
    unresolved_conflict: Literal["ABSTAIN"] = "ABSTAIN"
    invalid_synthesis: Literal["ABSTAIN"] = "ABSTAIN"
    max_concurrency: Annotated[int, Field(ge=1)] = 1
    max_attempts_per_role: Literal[1] = 1          # no silent retries or best-of-n (§7.8)
    tools: tuple[str, ...] = ()
    total_timeout_ms: Annotated[int, Field(gt=0)] | None = None
    analyst_cutoff_ms: Annotated[int, Field(gt=0)] | None = None
    max_run_cost_usd: DecimalStr | None = None
    daily_cost_cap_usd: DecimalStr | None = None

    def role(self, role_id: str) -> AgentRole:
        return next(r for r in self.roles if r.role_id == role_id)

    def roles_of(self, kind: RoleKind) -> tuple[AgentRole, ...]:
        return tuple(r for r in self.roles if r.kind is kind)

    @model_validator(mode="after")
    def _structure(self) -> Self:
        problems = self.structural_problems()
        if problems:
            raise ValueError("; ".join(problems))
        if self.enabled:
            lock = self.lock_problems()
            if lock:
                raise ValueError("enabled graph is not lockable: " + "; ".join(lock))
        return self

    def structural_problems(self) -> list[str]:
        p: list[str] = []
        ids = [r.role_id for r in self.roles]
        if len(ids) != len(set(ids)):
            p.append("role ids must be unique")
        known = set(ids)
        for r in self.roles:
            if missing := set(r.depends_on) - known:
                p.append(f"{r.role_id} depends on unknown roles {sorted(missing)}")
            if r.role_id in r.depends_on:
                p.append(f"{r.role_id} depends on itself")
            if bad := set(r.tools) - set(self.tools):
                p.append(f"{r.role_id} uses tools outside the graph allowlist {sorted(bad)}")
        if bad := set(self.tools) - SERVER_TOOL_ALLOWLIST:
            p.append(f"tools not in server allowlist {sorted(bad)}")
        if _has_cycle({r.role_id: r.depends_on for r in self.roles}):
            p.append("role dependencies contain a cycle")

        req, opt = set(self.required_roles), set(self.optional_roles)
        if req & opt:
            p.append(f"roles both required and optional {sorted(req & opt)}")
        analysts = {r.role_id for r in self.roles if r.kind in (RoleKind.ANALYST, RoleKind.FILTER)}
        if req | opt != analysts:
            p.append("required + optional roles must equal the enabled analyst/filter roles")

        synth = self.roles_of(RoleKind.SYNTHESIS)
        reviewers = self.roles_of(RoleKind.REVIEWER)
        filters = self.roles_of(RoleKind.FILTER)
        if self.capability_stage is CapabilityStage.FILTER_ONLY:
            if len(filters) != 1 or len(self.roles) != 1:
                p.append("FILTER_ONLY uses exactly one filter role and nothing else")
            if self.synthesis_required or self.reviewer_enabled:
                p.append("FILTER_ONLY has no synthesis or reviewer")
            if self.required_roles != tuple(r.role_id for r in filters):
                p.append("FILTER_ONLY filter role must be required")
        else:
            if filters:
                p.append("filter roles are only for FILTER_ONLY")
            if self.synthesis_required != bool(synth) or len(synth) > 1:
                p.append("exactly one synthesis role when synthesis_required, none otherwise")
            for s in synth:
                if set(s.depends_on) != analysts:
                    p.append("synthesis must depend on every analyst (after report validation)")
            if self.capability_stage is CapabilityStage.MULTI_ANALYST_FILTER and not self.synthesis_required:
                p.append("MULTI_ANALYST_FILTER requires synthesis")
            for a in self.roles_of(RoleKind.ANALYST):
                if a.depends_on:
                    p.append(f"analyst {a.role_id} must be independent in the first round")
        if self.reviewer_enabled != bool(reviewers):
            p.append("reviewer roles must match reviewer_enabled")
        return p

    def lock_problems(self) -> list[str]:
        """Rules that must hold before a graph is locked or enabled (§7.6, §7.9: a draft with nulls cannot start)."""
        p: list[str] = []
        if self.capability_stage is CapabilityStage.BOUNDED_PROPOSAL:
            p.append("BOUNDED_PROPOSAL needs a template compiler and is not enabled in this version")
        for r in self.roles:
            for field in ("model_id", "prompt_version", "schema_version", "timeout_ms", "max_output_tokens"):
                if getattr(r, field) is None:
                    p.append(f"{r.role_id}.{field} is null")
        for field in ("total_timeout_ms", "max_run_cost_usd", "daily_cost_cap_usd"):
            if getattr(self, field) is None:
                p.append(f"{field} is null")
        multi = self.capability_stage is not CapabilityStage.FILTER_ONLY
        if multi and self.analyst_cutoff_ms is None:
            p.append("analyst_cutoff_ms is null")
        if p:
            return p

        assert self.total_timeout_ms is not None and self.max_run_cost_usd is not None
        assert self.daily_cost_cap_usd is not None
        if self.max_run_cost_usd > self.daily_cost_cap_usd:
            p.append("max_run_cost_usd exceeds daily_cost_cap_usd")
        if self.max_run_cost_usd <= 0:
            p.append("max_run_cost_usd must be positive")
        cutoff = self.analyst_cutoff_ms if multi else self.total_timeout_ms
        assert cutoff is not None
        if cutoff > self.total_timeout_ms:
            p.append("analyst_cutoff_ms exceeds total_timeout_ms")
        for r in self.roles:
            assert r.timeout_ms is not None
            if r.kind in (RoleKind.ANALYST, RoleKind.FILTER) and r.timeout_ms > cutoff:
                p.append(f"{r.role_id}.timeout_ms exceeds the analyst cutoff")
            if r.kind in (RoleKind.SYNTHESIS, RoleKind.REVIEWER) and r.timeout_ms > self.total_timeout_ms - cutoff:
                p.append(f"{r.role_id}.timeout_ms does not fit after the analyst cutoff")
        return p

    def graph_hash(self) -> str:
        """sha256 of the canonical JSON form; changing any field makes a new graph/experiment version."""
        canon = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()


def _has_cycle(edges: dict[str, tuple[str, ...]]) -> bool:
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(m) for m in edges.get(n, ()) if m in edges):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in edges)


# --- requests and model outputs (§7.2, §7.7) --------------------------------

class AnalysisRequest(_Strict):
    request_id: Id
    run_id: Id
    experiment_id: Id
    arm_id: Id
    proposal_id: Id | None = None          # future drafts use request_id before a proposal exists
    snapshot_id: Id
    context_manifest_hash: Sha256
    as_of: AwareDatetime
    valid_until: AwareDatetime
    graph_hash: Sha256
    task: Annotated[str, Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _times(self) -> Self:
        if self.valid_until <= self.as_of:
            raise ValueError("valid_until must be after as_of")
        return self


class FilterResponse(_Strict):
    """v1 filter output as §7.2 plus a schema_version; call metadata is recorded by the backend."""
    schema_version: Literal["filter_response.v1"] = "filter_response.v1"
    proposal_id: Id
    snapshot_id: Id
    decision: FilterDecision
    reason_code: ReasonCode
    reason: Text
    evidence_refs: Refs = ()
    policy_version: Id

    @model_validator(mode="after")
    def _model_codes(self) -> Self:
        if self.reason_code not in MODEL_REASON_CODES:
            raise ValueError(f"reason_code {self.reason_code} is assigned by the backend, not the model")
        return self


class Finding(_Strict):
    claim_id: Id
    kind: FindingKind
    summary: Text
    evidence_refs: Refs = ()


class AgentAnalysis(_Strict):
    schema_version: Literal["agent_analysis.v1"] = "agent_analysis.v1"
    run_id: Id
    role_id: str
    snapshot_id: Id
    assessment: Assessment
    findings: tuple[Finding, ...] = Field(default=(), max_length=MAX_FINDINGS)
    counterevidence_refs: Refs = ()
    missing_inputs: tuple[str, ...] = ()
    uncertainty: Text

    @model_validator(mode="after")
    def _unique_claims(self) -> Self:
        ids = [f.claim_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("claim ids must be unique within a report")
        return self


class SynthesisResult(_Strict):
    schema_version: Literal["synthesis_result.v1"] = "synthesis_result.v1"
    run_id: Id
    cited_report_ids: tuple[str, ...] = Field(min_length=1)
    cited_claim_ids: tuple[Id, ...] = ()
    evidence_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=4 * MAX_REFS)] = ()
    thesis: Annotated[str, Field(min_length=1, max_length=1000)]
    unresolved_conflicts: tuple[Text, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    suggested_disposition: Disposition


class ProposalDraft(_Strict):
    """Future BOUNDED_PROPOSAL output; compiled by code before any execution path (§7.6). Not enabled."""
    request_id: Id
    snapshot_id: Id
    side: Literal["LONG", "SHORT", "NO_TRADE"]
    strategy_template_id: Id
    entry_template_id: Id | None = None
    exit_template_id: Id | None = None
    thesis: Text
    evidence_refs: Refs = ()
    expires_at: AwareDatetime


# --- backend-owned envelopes (§7.7, §7.8) -----------------------------------

class AttemptRecord(_Strict):
    attempt_id: Id
    run_id: Id
    role_id: str
    status: AttemptStatus
    input_hash: Sha256
    output_hash: Sha256 | None = None
    model_id: str | None = None
    provider_response_id: str | None = None
    prompt_hash: Sha256 | None = None
    request_at: AwareDatetime
    response_at: AwareDatetime | None = None
    validated_at: AwareDatetime | None = None
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    cost_usd: DecimalStr | None = None
    reserved_cost_usd: DecimalStr | None = None
    error_category: str | None = None

    @model_validator(mode="after")
    def _unknown_keeps_liability(self) -> Self:
        if self.status is AttemptStatus.UNKNOWN:
            if self.cost_usd is not None:
                raise ValueError("UNKNOWN attempts keep their reservation; actual cost is not known")
            if self.reserved_cost_usd is None or self.reserved_cost_usd <= Decimal(0):
                raise ValueError("UNKNOWN attempts must keep a positive cost reservation")
        if self.status is AttemptStatus.SUCCESS and (self.output_hash is None or self.validated_at is None):
            raise ValueError("SUCCESS needs an output hash and validation time")
        return self


class PolicyDecision(_Strict):
    """Final decision envelope; the backend owns scope and timestamps."""
    schema_version: Literal["policy_decision.v1"] = "policy_decision.v1"
    reason_codes_version: Literal["reason_codes.v1"] = "reason_codes.v1"
    run_id: Id
    proposal_id: Id | None
    decision: FilterDecision
    reason_code: ReasonCode
    evidence_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=4 * MAX_REFS)] = ()
    graph_hash: Sha256
    policy_version: Id
    validation_state: Literal["VALID", "INVALID", "NOT_EVALUATED"]
    validated_at: AwareDatetime
    partial_context: bool = False
    missing_roles: tuple[str, ...] = ()

    @property
    def reason_kind(self) -> ReasonKind:
        return REASON_KIND[self.reason_code]

    @model_validator(mode="after")
    def _system_reasons_abstain(self) -> Self:
        if self.reason_kind is not ReasonKind.MARKET and self.decision is not FilterDecision.ABSTAIN:
            raise ValueError("system and eligibility reasons must ABSTAIN; they are never a market VETO or ALLOW")
        return self


class AnalysisRun(_Strict):
    run_id: Id
    graph_hash: Sha256
    request_id: Id
    state: RunState = RunState.CREATED
    revision: Annotated[int, Field(ge=0)] = 0
    started_at: AwareDatetime
    effective_deadline: AwareDatetime

    def transition(self, new: RunState) -> AnalysisRun:
        """Compare-and-set style transition; terminal states never change."""
        if new not in RUN_TRANSITIONS[self.state]:
            raise ValueError(f"illegal run transition {self.state} -> {new}")
        return self.model_copy(update={"state": new, "revision": self.revision + 1})
