"""The AI filter of the Phase 5 path A paired run (docs/phase-5-paired-plan.md, §7.2–7.4).

One provider call per question, no tools, no retries, no fallback model. The model sees only
the frozen quant snapshot and answers ALLOW / VETO / ABSTAIN in the strict filter_response.v1
shape. Everything the backend owns (ids, times, tokens, cost, prompt hash) is recorded here and
never taken from the model. Every failure ends as an ABSTAIN with a system reason through
``decide_filter_only``, so it is never counted as a market VETO.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import ValidationError

from trading.analysis.graphs import CONFIG_DIR, load_graph
from trading.analysis.policy import Runtime, decide_filter_only
from trading.contracts.analysis import (
    MODEL_REASON_CODES,
    AgentGraphConfig,
    AnalysisRequest,
    AttemptRecord,
    AttemptStatus,
    FilterDecision,
    FilterResponse,
    PolicyDecision,
)

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam

EXPERIMENT_ID = "pathA-p5-r1"
GRAPH_FILE = CONFIG_DIR / "pathA_p5_filter.yaml"
PROMPT_VERSION = "pathA_p5_filter_prompt_v1"
FILTER_POLICY = "pathA_p5_filter_v1"            # the policy_version the model must echo
EFFORT: Literal["medium"] = "medium"
# Not ANTHROPIC_API_KEY: that name belongs to the session running this code.
KEY_ENV = "P5_ANTHROPIC_KEY"
# USD per million tokens (input, output): Anthropic list price, checked 2026-10-16. Reports use
# the tokens the API returned; a price change means a new revision, not a silent edit.
PRICES: dict[str, tuple[Decimal, Decimal]] = {"claude-opus-5-5": (Decimal("4"), Decimal("20"))}

SYSTEM = """You review one proposed change to a simulated BTC spot portfolio (no real money).
The code strategy owns the target weight, timing and sizing. You cannot change them.
Use only the SNAPSHOT given. Do not use remembered prices, later events or any other source.
Return ALLOW if the snapshot gives no supported reason against the question.
Return VETO only with a reason_code and evidence_refs that name keys present in the SNAPSHOT evidence list.
Return ABSTAIN if the snapshot is insufficient or contradictory.
Answer only with the JSON object of the supplied schema. Copy proposal_id, snapshot_id and policy_version exactly."""

QUESTIONS = {
    "filter": "The code target raises the BTC weight of this portfolio from {current} to {target} at the next 1h open. "
              "Should the increase go ahead (ALLOW) or not (VETO)?",
    "diagnostic": "The code target keeps the BTC weight at {target} into the next day. "
                  "Would you keep this exposure (ALLOW) or not (VETO)? This answer is recorded only and changes nothing.",
}


def output_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": ["proposal_id", "snapshot_id", "decision", "reason_code", "reason", "evidence_refs", "policy_version"],
            "properties": {
                "proposal_id": {"type": "string"}, "snapshot_id": {"type": "string"},
                "decision": {"type": "string", "enum": [d.value for d in FilterDecision]},
                "reason_code": {"type": "string", "enum": sorted(c.value for c in MODEL_REASON_CODES)},
                "reason": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "policy_version": {"type": "string"}}}


def prompt_hash() -> str:
    canon = json.dumps({"v": PROMPT_VERSION, "system": SYSTEM, "questions": QUESTIONS, "schema": output_schema(),
                        "effort": EFFORT}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def load_filter_graph(path: Path = GRAPH_FILE) -> AgentGraphConfig:
    g = load_graph(path)
    if not g.enabled or g.lock_problems():
        raise ValueError("the pathA-p5 filter graph must be enabled and locked")
    return g


# --- snapshot -------------------------------------------------------------------------------

def snapshot(decided_on: str, inputs: dict[str, Any], closes: list[tuple[str, float]], weight: float,
             drawdown: float) -> dict[str, Any]:
    """The frozen quant-only context. ``inputs`` is the decision_inputs snapshot shared with pathA-v1.1/v1.2."""
    ev: dict[str, Any] = {f"feature:{k}": v for k, v in inputs.items() if k != "funding_window"}
    for w in inputs.get("funding_window", []):
        ev[f"funding:{w['day']}"] = {"per_day": w["funding"], "source": w["source"]}
    for day, c in closes[-30:]:
        ev[f"close:{day}"] = c
    ev["portfolio:weight"] = weight
    ev["portfolio:drawdown"] = round(drawdown, 6)
    body = json.dumps(ev, sort_keys=True, separators=(",", ":"))
    return {"snapshot_id": f"p5snap:{decided_on}:{hashlib.sha256(body.encode()).hexdigest()[:16]}",
            "context_profile": "quant_only", "decided_on": decided_on, "evidence": ev}


# --- provider ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderReply:
    text: str | None
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None          # TIMEOUT | PROVIDER_ERROR | REFUSAL | TRUNCATED | NO_TEXT


class Provider(Protocol):
    model_id: str

    def ask(self, system: str, user: str, schema: dict[str, Any], max_tokens: int, timeout_s: float) -> ProviderReply: ...


class AnthropicProvider:
    """Claude through the official SDK. SDK retries are off: one attempt, as §7.4 requires.
    No ``fallbacks``: the plan forbids a silent model swap, so a refusal is a reliability failure."""

    def __init__(self, model_id: str, api_key: str):
        import anthropic

        self.model_id = model_id
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def ask(self, system: str, user: str, schema: dict[str, Any], max_tokens: int, timeout_s: float) -> ProviderReply:
        a = self._anthropic
        cfg: OutputConfigParam = {"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}}
        msgs: list[MessageParam] = [{"role": "user", "content": user}]
        try:
            r = self._client.with_options(timeout=timeout_s).messages.create(
                model=self.model_id, max_tokens=max_tokens, system=system, messages=msgs, output_config=cfg)
        except a.APITimeoutError:
            return ProviderReply(None, error="TIMEOUT")
        except (a.APIStatusError, a.APIConnectionError) as e:
            return ProviderReply(None, error=f"PROVIDER_ERROR:{type(e).__name__}")
        usage = (r.usage.input_tokens, r.usage.output_tokens)
        if r.stop_reason == "refusal":
            return ProviderReply(None, r.id, *usage, error="REFUSAL")
        if r.stop_reason == "max_tokens":
            return ProviderReply(None, r.id, *usage, error="TRUNCATED")
        text = next((b.text for b in r.content if b.type == "text"), None)
        return ProviderReply(text, r.id, *usage, error=None if text else "NO_TEXT")


class MockProvider:
    """Recorded answers for tests and dry runs; never used for the experiment's evidence."""

    def __init__(self, decide: Callable[[dict[str, Any]], dict[str, Any] | str | None] | str = "ALLOW"):
        self.model_id = "mock"
        self._decide = decide
        self.calls: list[dict[str, Any]] = []

    def ask(self, system: str, user: str, schema: dict[str, Any], max_tokens: int, timeout_s: float) -> ProviderReply:
        req = json.loads(user.split("REQUEST:", 1)[1].split("\nSNAPSHOT:", 1)[0])
        self.calls.append(req)
        out = self._decide(req) if callable(self._decide) else {
            "proposal_id": req["proposal_id"], "snapshot_id": req["snapshot_id"], "decision": self._decide,
            "reason_code": "SUPPORTED_CONTEXT" if self._decide == "ALLOW" else "CONTEXT_CONFLICT",
            "reason": "mock", "evidence_refs": [], "policy_version": FILTER_POLICY}
        if out is None:
            return ProviderReply(None, error="TIMEOUT")
        return ProviderReply(out if isinstance(out, str) else json.dumps(out), "mock", 1000, 200)


def cost_usd(model_id: str, input_tokens: int | None, output_tokens: int | None) -> Decimal:
    if model_id not in PRICES:
        return Decimal(0)
    pin, pout = PRICES[model_id]
    return ((pin * (input_tokens or 0) + pout * (output_tokens or 0)) / Decimal(1_000_000)).quantize(Decimal("1e-9"))


# --- one question ---------------------------------------------------------------------------

def ask(kind: str, proposal_id: str, snap: dict[str, Any], current: float, target: float, provider: Provider,
        graph: AgentGraphConfig, budget_left: Decimal, clock: Callable[[], datetime]) -> dict[str, Any]:
    """Ask one filter or diagnostic question; returns a JSON-safe record with the backend's decision."""
    role = graph.role("filter")
    assert role.timeout_ms is not None and role.max_output_tokens is not None and graph.max_run_cost_usd is not None
    if provider.model_id not in ("mock", role.model_id):
        raise ValueError(f"provider model {provider.model_id} is not the locked {role.model_id}")
    start = clock()
    deadline = start + timedelta(milliseconds=role.timeout_ms)
    evidence = frozenset(snap["evidence"])
    manifest = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    req = AnalysisRequest(request_id=f"{proposal_id}:req", run_id=f"{proposal_id}:run", experiment_id=EXPERIMENT_ID,
                          arm_id="pathA-p5-r1.ai", proposal_id=proposal_id, snapshot_id=snap["snapshot_id"],
                          context_manifest_hash=manifest, as_of=start, valid_until=deadline, graph_hash=graph.graph_hash(),
                          task=QUESTIONS[kind].format(current=current, target=target))
    budget_ok = budget_left >= graph.max_run_cost_usd
    status, resp, attempt = AttemptStatus.CANCELLED, None, None
    if budget_ok:
        user = ("REQUEST:" + json.dumps({"proposal_id": proposal_id, "snapshot_id": snap["snapshot_id"],
                                         "policy_version": FILTER_POLICY, "question": req.task}, sort_keys=True)
                + "\nSNAPSHOT:" + json.dumps(snap["evidence"], sort_keys=True))
        reply = provider.ask(SYSTEM, user, output_schema(), role.max_output_tokens, role.timeout_ms / 1000)
        done = clock()
        error = reply.error
        if error is None and done >= deadline:
            error = "LATE"
        if error is None:
            try:
                raw = json.loads(reply.text or "")
                resp = FilterResponse.model_validate(raw)
                if resp.reason_code not in MODEL_REASON_CODES or resp.policy_version != FILTER_POLICY:
                    raise ValueError("reason_code or policy_version outside the allowlist")
                status = AttemptStatus.SUCCESS
            except (ValueError, ValidationError) as e:
                resp, status, error = None, AttemptStatus.INVALID, f"INVALID:{type(e).__name__}"
        else:
            status = AttemptStatus.TIMEOUT if error in ("TIMEOUT", "LATE") else AttemptStatus.PROVIDER_ERROR
        cost = cost_usd(provider.model_id, reply.input_tokens, reply.output_tokens)
        attempt = AttemptRecord(
            attempt_id=f"{proposal_id}:a1", run_id=req.run_id, role_id="filter", status=status,
            input_hash=hashlib.sha256(user.encode()).hexdigest(),
            output_hash=hashlib.sha256((reply.text or "").encode()).hexdigest() if reply.text else None,
            model_id=provider.model_id, provider_response_id=reply.response_id, prompt_hash=prompt_hash(),
            request_at=start, response_at=done, validated_at=done if status is AttemptStatus.SUCCESS else None,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_usd=cost, error_category=error)
    decided_at = clock()
    rt = Runtime(now=decided_at, effective_deadline=deadline, budget_permitted=budget_ok, evidence=evidence)
    decision: PolicyDecision = decide_filter_only(req, graph, status, resp, rt)
    return {"kind": kind, "proposal_id": proposal_id, "snapshot_id": snap["snapshot_id"], "current": current,
            "target": target, "request": req.model_dump(mode="json"),
            "response": None if resp is None else resp.model_dump(mode="json"),
            "attempt": None if attempt is None else attempt.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"), "decided_at": decided_at.isoformat(),
            "cost_usd": "0" if attempt is None else str(attempt.cost_usd)}
