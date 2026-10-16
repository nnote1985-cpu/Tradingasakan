"""The AI proposer of the Phase 5 r2 run on path A (docs/phase-5-r2-plan.md, BUILDPLAN §7.6).

BOUNDED_PROPOSAL with one template, ``pathA_spot_weight_v1``: once a day the model proposes a
BTC spot weight in {0, 0.5, 1} with an allowlisted reason. Code compiles the answer
(``compile_proposal``); the model never sees an exchange, sizes nothing and times nothing. The
snapshot is quant_only and never contains Code's suggested weight; that weight is recorded beside
the proposal for the report only. Every failure (invalid answer, late, refusal, provider error,
budget cap) keeps the current weight and is logged in its own category, never as a decision.

One provider call per proposal, no tools, no retries, no fallback model. The provider classes,
the cost table and the snapshot shape are shared with r1 (``trading.analysis.pathA_filter``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trading.analysis import pathA_filter as pf
from trading.analysis.graphs import CONFIG_DIR, load_graph
from trading.contracts.analysis import (
    PROPOSAL_TEMPLATES,
    SPOT_WEIGHTS,
    AgentGraphConfig,
    AnalysisRequest,
    AttemptRecord,
    AttemptStatus,
    CapabilityStage,
    ProposalReasonCode,
    SpotWeightProposal,
)

EXPERIMENT_ID = "pathA-p5-r2"
GRAPH_FILE = CONFIG_DIR / "pathA_p5_r2_proposer.yaml"
TEMPLATE_ID = "pathA_spot_weight_v1"
PROMPT_VERSION = "pathA_p5_r2_proposer_prompt_v1"
PROPOSAL_POLICY = "pathA_p5_r2_proposal_v1"     # the policy_version the model must echo
EFFORT = pf.EFFORT
KEY_ENV = pf.KEY_ENV                              # r2 reuses r1's key; no new secret
# Inputs that carry Code's own answer: never shown to the proposer (plan: quant_only without it).
CODE_ANSWER_KEYS = frozenset({"suggested_btc_weight"})

SYSTEM = """You manage the BTC weight of one simulated BTC spot portfolio (no real money).
Each day you propose the target BTC weight for the next day: 0 (all USDT), 0.5 or 1 (all BTC).
Long or flat only. You cannot choose leverage, symbols, prices or timing; code fills at the next 1h open.
Loss limits are enforced by code and you cannot change them.
Use only the SNAPSHOT given. Do not use remembered prices, later events or any other source.
Give a reason_code from the schema, a reason of at most 500 characters, and at most 8 evidence_refs that name keys present in the SNAPSHOT.
Answer only with the JSON object of the supplied schema. Copy proposal_id, snapshot_id, template_id and policy_version exactly."""

TASK = ("Propose the target BTC weight of this portfolio for the next day. Its current target weight is {current}. "
        "Choose 0, 0.5 or 1.")


def output_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": ["proposal_id", "snapshot_id", "template_id", "target_weight", "reason_code", "reason",
                         "evidence_refs", "policy_version"],
            "properties": {
                "proposal_id": {"type": "string"}, "snapshot_id": {"type": "string"},
                "template_id": {"type": "string", "enum": [TEMPLATE_ID]},
                "target_weight": {"type": "number", "enum": list(SPOT_WEIGHTS)},
                "reason_code": {"type": "string", "enum": [c.value for c in ProposalReasonCode]},
                "reason": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "policy_version": {"type": "string"}}}


def prompt_hash() -> str:
    canon = json.dumps({"v": PROMPT_VERSION, "system": SYSTEM, "task": TASK, "schema": output_schema(),
                        "effort": EFFORT}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def load_proposer_graph(path: Path = GRAPH_FILE) -> AgentGraphConfig:
    g = load_graph(path)
    if (not g.enabled or g.lock_problems() or g.capability_stage is not CapabilityStage.BOUNDED_PROPOSAL
            or g.proposal_template_id != TEMPLATE_ID or TEMPLATE_ID not in PROPOSAL_TEMPLATES):
        raise ValueError("the pathA-p5-r2 proposer graph must be an enabled, locked BOUNDED_PROPOSAL "
                         f"graph for {TEMPLATE_ID}")
    return g


def snapshot(decided_on: str, inputs: dict[str, Any], closes: list[tuple[str, float]], weight: float,
             drawdown: float) -> dict[str, Any]:
    """r1's quant_only snapshot without Code's answer; ``weight`` and ``drawdown`` are the AI arm's own."""
    s = pf.snapshot(decided_on, {k: v for k, v in inputs.items() if k not in CODE_ANSWER_KEYS}, closes, weight, drawdown)
    return s | {"snapshot_id": s["snapshot_id"].replace("p5snap:", "p5r2snap:", 1)}


# --- the template compiler ------------------------------------------------------------------

class ProposalRejected(ValueError):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def compile_proposal(text: str | None, proposal_id: str, snap: dict[str, Any]) -> SpotWeightProposal:
    """Check one answer against pathA_spot_weight_v1. Raises ``ProposalRejected`` with the category."""
    try:
        raw = json.loads(text or "")
    except ValueError:
        raise ProposalRejected("INVALID_JSON") from None
    if isinstance(raw, dict) and raw.get("template_id") not in (None, TEMPLATE_ID):
        raise ProposalRejected("UNKNOWN_TEMPLATE")
    try:
        p = SpotWeightProposal.model_validate(raw)
    except ValidationError:
        raise ProposalRejected("INVALID_SCHEMA") from None
    if p.proposal_id != proposal_id or p.snapshot_id != snap["snapshot_id"] or p.policy_version != PROPOSAL_POLICY:
        raise ProposalRejected("MISMATCHED_IDS")
    if set(p.evidence_refs) - set(snap["evidence"]):
        raise ProposalRejected("UNKNOWN_EVIDENCE")
    return p


def user_message(proposal_id: str, snap: dict[str, Any], current: float) -> str:
    return ("REQUEST:" + json.dumps({"proposal_id": proposal_id, "snapshot_id": snap["snapshot_id"],
                                     "template_id": TEMPLATE_ID, "policy_version": PROPOSAL_POLICY,
                                     "question": TASK.format(current=current)}, sort_keys=True)
            + "\nSNAPSHOT:" + json.dumps(snap["evidence"], sort_keys=True))


# --- one proposal ---------------------------------------------------------------------------

def propose(proposal_id: str, snap: dict[str, Any], current: float, code_weight: float | None,
            provider: pf.Provider, graph: AgentGraphConfig, budget_left: Decimal,
            clock: Callable[[], datetime]) -> dict[str, Any]:
    """Ask for one proposal and compile it. Returns a JSON-safe record; ``outcome.accepted`` False keeps ``current``."""
    role = graph.role("proposer")
    assert role.timeout_ms is not None and role.max_output_tokens is not None and graph.max_run_cost_usd is not None
    if provider.model_id not in ("mock", role.model_id):
        raise ValueError(f"provider model {provider.model_id} is not the locked {role.model_id}")
    start = clock()
    deadline = start + timedelta(milliseconds=role.timeout_ms)
    manifest = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    req = AnalysisRequest(request_id=f"{proposal_id}:req", run_id=f"{proposal_id}:run", experiment_id=EXPERIMENT_ID,
                          arm_id="pathA-p5-r2.ai", proposal_id=proposal_id, snapshot_id=snap["snapshot_id"],
                          context_manifest_hash=manifest, as_of=start, valid_until=deadline, graph_hash=graph.graph_hash(),
                          task=TASK.format(current=current))
    resp: SpotWeightProposal | None = None
    attempt: AttemptRecord | None = None
    if budget_left < graph.max_run_cost_usd:
        error: str | None = "BUDGET_CAP"
    else:
        user = user_message(proposal_id, snap, current)
        reply = provider.ask(SYSTEM, user, output_schema(), role.max_output_tokens, role.timeout_ms / 1000)
        done = clock()
        error = reply.error
        if error is None and done >= deadline:
            error = "LATE"
        status = AttemptStatus.SUCCESS
        if error is None:
            try:
                resp = compile_proposal(reply.text, proposal_id, snap)
            except ProposalRejected as e:
                status, error = AttemptStatus.INVALID, f"INVALID:{e.category}"
        else:
            status = AttemptStatus.TIMEOUT if error in ("TIMEOUT", "LATE") else AttemptStatus.PROVIDER_ERROR
        attempt = AttemptRecord(
            attempt_id=f"{proposal_id}:a1", run_id=req.run_id, role_id="proposer", status=status,
            input_hash=hashlib.sha256(user.encode()).hexdigest(),
            output_hash=hashlib.sha256(reply.text.encode()).hexdigest() if reply.text else None,
            model_id=provider.model_id, provider_response_id=reply.response_id, prompt_hash=prompt_hash(),
            request_at=start, response_at=done, validated_at=done if status is AttemptStatus.SUCCESS else None,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
            cost_usd=pf.cost_usd(provider.model_id, reply.input_tokens, reply.output_tokens), error_category=error)
    decided_at = clock()
    accepted = resp is not None
    weight = resp.target_weight if resp is not None else current
    return {"proposal_id": proposal_id, "snapshot_id": snap["snapshot_id"], "template_id": TEMPLATE_ID,
            "current": current, "code_weight": code_weight, "request": req.model_dump(mode="json"),
            "response": None if resp is None else resp.model_dump(mode="json"),
            "attempt": None if attempt is None else attempt.model_dump(mode="json"),
            "outcome": {"accepted": accepted, "target_weight": weight, "error_category": error,
                        "reason_code": resp.reason_code.value if resp is not None else None},
            "decided_at": decided_at.isoformat(),
            "cost_usd": "0" if attempt is None or attempt.cost_usd is None else str(attempt.cost_usd)}
