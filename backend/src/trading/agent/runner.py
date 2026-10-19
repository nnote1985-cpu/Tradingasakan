"""One decision of agent-v1: three analysts, the Decider, then the Veto (docs/agent-book-plan.md §4, step 2).

Every model call becomes a :class:`RunRecord` and every tool call a
:class:`ToolCall`, shaped like the rows of ``trading.agent_runs`` and
``trading.agent_tool_calls``, so a decision can be replayed from what the
models actually saw. Rules kept here:

- the budget is checked before each call by reserving the role's
  ``reserve_usd``; a call that cannot reserve it is not made
  (BUDGET_BLOCKED), never silently swapped for a cheaper one;
- one attempt per call, SDK retries off; a failure ends that run
  (TIMEOUT, ERROR, SCHEMA_INVALID) and the decision goes on without it;
- the Veto runs only on BUY or SELL; HOLD has nothing to stop;
- signals the Decider asks for go through :mod:`trading.agent.signals`
  and are dropped, with the reason, when invalid.

Nothing here touches the database or an exchange account.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from trading.agent import market
from trading.agent.roles import ANALYSTS, DECIDER, PRICES, PROMPT_VERSION, VETO, WEB_SEARCH_USD, Role
from trading.agent.signals import HOUR_MS, MAX_ACTIVE, Signal, check_new, parse_params

Clock = Callable[[], datetime]


class ModelClient(Protocol):
    def create(self, timeout_s: float, **kwargs: Any) -> Any: ...


class AnthropicClient:
    """The official SDK, one attempt per call. The base URL is pinned so ANTHROPIC_BASE_URL cannot redirect it."""

    def __init__(self, api_key: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com", max_retries=0)

    def create(self, timeout_s: float, **kwargs: Any) -> Any:
        return self._client.with_options(timeout=timeout_s).messages.create(**kwargs)


@dataclass
class Budget:
    """Spend already counted today and this month. A call first holds its reserve, so the three analysts
    running at once cannot all pass the check against the same headroom; ``settle`` swaps it for the real cost."""
    daily_cap: Decimal
    monthly_cap: Decimal
    spent_today: Decimal = Decimal(0)
    spent_month: Decimal = Decimal(0)
    held: Decimal = Decimal(0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def reserve(self, usd: Decimal) -> bool:
        with self._lock:
            if self.spent_today + self.held + usd > self.daily_cap or self.spent_month + self.held + usd > self.monthly_cap:
                return False
            self.held += usd
            return True

    def settle(self, reserved: Decimal, actual: Decimal) -> None:
        with self._lock:
            self.held -= reserved
            self.spent_today += actual
            self.spent_month += actual


@dataclass
class ToolCall:
    seq: int
    tool: str
    request: dict[str, Any]
    called_at: str
    response: Any = None
    response_sha256: str | None = None
    error: str | None = None


@dataclass
class RunRecord:
    id: str
    decision_id: str
    role: str
    model: str
    prompt_version: str
    started_at: str
    finished_at: str | None = None
    input_tokens: int = 0           # uncached + cache write + cache read, all rounds
    output_tokens: int = 0
    web_searches: int = 0
    cost_usd: Decimal = Decimal(0)
    outcome: str = "OK"             # OK | TIMEOUT | ERROR | SCHEMA_INVALID | BUDGET_BLOCKED
    error: str | None = None
    output: dict[str, Any] | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["cost_usd"] = str(self.cost_usd)
        return d


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def call_cost(model: str, usage: Any) -> tuple[Decimal, int, int, int]:
    """(USD, input tokens incl. cache, output tokens, web searches) of one response, at list price."""
    pin, pout, pread = PRICES[model]
    plain = _get(usage, "input_tokens") or 0
    write = _get(usage, "cache_creation_input_tokens") or 0
    read = _get(usage, "cache_read_input_tokens") or 0
    out = _get(usage, "output_tokens") or 0
    searches = _get(_get(usage, "server_tool_use"), "web_search_requests") or 0
    usd = (pin * plain + pin * Decimal("1.25") * write + pread * read + pout * out) / Decimal(1_000_000) + WEB_SEARCH_USD * searches
    return usd.quantize(Decimal("1e-6")), plain + write + read, out, searches


class Tools:
    """Runs the client tools the analysts ask for against :mod:`trading.agent.market`."""

    def __init__(self, get_json: market.GetJson, clock: Clock):
        self.get_json = get_json
        self.clock = clock

    def run(self, name: str, args: Mapping[str, Any]) -> Any:
        if name == "get_candles":
            return market.candles(self.get_json, str(args["interval"]), int(args["limit"]), int(self.clock().timestamp() * 1000))
        if name == "get_ticker_24h":
            return market.ticker_24h(self.get_json)
        if name == "get_order_book":
            return market.order_book(self.get_json, int(args["limit"]))
        if name == "get_derivatives":
            return market.derivatives(self.get_json)
        raise ValueError(f"unknown tool {name}")


def _iso(clock: Clock) -> str:
    return clock().astimezone(UTC).isoformat()


def _log_server_blocks(content: Sequence[Any], rec: RunRecord, clock: Clock) -> None:
    """Web searches run on Anthropic's side; log what was asked and what came back, like client tools."""
    asked = {}
    for b in content:
        t = _get(b, "type")
        if t == "server_tool_use":
            asked[_get(b, "id")] = (_get(b, "name"), _get(b, "input") or {})
        elif t == "web_search_tool_result":
            name, req = asked.get(_get(b, "tool_use_id"), ("web_search", {}))
            raw = _get(b, "content")
            call = ToolCall(len(rec.tool_calls) + 1, str(name), dict(req), _iso(clock))
            if isinstance(raw, list):
                call.response = [{"url": _get(r, "url"), "title": _get(r, "title"), "page_age": _get(r, "page_age")} for r in raw]
                call.response_sha256 = _sha(call.response)
            else:
                call.error = str(_get(raw, "error_code") or "web_search_error")
            rec.tool_calls.append(call)


def _answer(content: Sequence[Any]) -> Any:
    """The JSON answer: the text after the last search or tool block. Search citations can split it into
    several text blocks, so they are joined; text written before a search is not part of the answer."""
    tail: list[str] = []
    for b in content:
        if _get(b, "type") == "text":
            tail.append(str(_get(b, "text") or ""))
        elif _get(b, "type") != "thinking":
            tail = []
    try:
        return json.loads("".join(tail))
    except json.JSONDecodeError:
        return None


def run_role(role: Role, prompt: str, client: ModelClient, tools: Tools, budget: Budget, decision_id: str,
             clock: Clock) -> RunRecord:
    """One role to its JSON answer: the tool loop, pause_turn resumes, cost and every tool call logged."""
    rec = RunRecord(str(uuid.uuid4()), decision_id, role.name, role.model, PROMPT_VERSION, _iso(clock))
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    client_tools = {t["name"] for t in role.tools if "input_schema" in t}

    def finish(outcome: str, error: str | None = None) -> RunRecord:
        rec.outcome, rec.error, rec.finished_at = outcome, error, _iso(clock)
        return rec

    for _ in range(role.max_rounds):
        if not budget.reserve(role.reserve_usd):
            return finish("BUDGET_BLOCKED")
        kwargs: dict[str, Any] = {
            "model": role.model, "max_tokens": role.max_tokens, "system": role.system, "messages": messages,
            "output_config": {"effort": role.effort, "format": {"type": "json_schema", "schema": role.schema}},
            "cache_control": {"type": "ephemeral"},
        }
        if role.tools:
            kwargs["tools"] = list(role.tools)
        try:
            r = client.create(role.timeout_s, **kwargs)
        except Exception as e:  # noqa: BLE001 - every provider failure ends this run, by name
            budget.settle(role.reserve_usd, Decimal(0))   # a failed call may still be billed; the API's usage report is the truth
            name = type(e).__name__
            return finish("TIMEOUT" if "Timeout" in name else "ERROR", name)
        usd, tin, tout, searches = call_cost(role.model, r.usage)
        budget.settle(role.reserve_usd, usd)
        rec.cost_usd += usd
        rec.input_tokens += tin
        rec.output_tokens += tout
        rec.web_searches += searches
        _log_server_blocks(r.content, rec, clock)
        stop = r.stop_reason
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": r.content})
            continue
        if stop == "tool_use":
            messages.append({"role": "assistant", "content": r.content})
            results = []
            for b in r.content:
                if _get(b, "type") != "tool_use":
                    continue
                name, args = str(_get(b, "name")), dict(_get(b, "input") or {})
                call = ToolCall(len(rec.tool_calls) + 1, name, args, _iso(clock))
                try:
                    if name not in client_tools:
                        raise ValueError(f"tool {name} is not allowed for {role.name}")
                    call.response = tools.run(name, args)
                    call.response_sha256 = _sha(call.response)
                    results.append({"type": "tool_result", "tool_use_id": _get(b, "id"), "content": json.dumps(call.response)})
                except Exception as e:  # noqa: BLE001 - the model is told the tool failed and may carry on
                    call.error = f"{type(e).__name__}: {e}"[:500]
                    results.append({"type": "tool_result", "tool_use_id": _get(b, "id"), "content": call.error, "is_error": True})
                rec.tool_calls.append(call)
            messages.append({"role": "user", "content": results})
            continue
        if stop != "end_turn":
            return finish("ERROR", f"stop_reason:{stop}")
        out = _answer(r.content)
        if out is None:
            return finish("SCHEMA_INVALID", "no JSON answer")
        if not isinstance(out, dict) or not set(role.schema["required"]) <= set(out):
            return finish("SCHEMA_INVALID", "missing required fields")
        rec.output = out
        return finish("OK")
    return finish("ERROR", "max_rounds")


@dataclass
class Trigger:
    signal_id: str
    condition: str
    params: dict[str, str]
    fired_at: str
    reason: str                     # e.g. "FIRED on the 1h bar closing at ..."


@dataclass
class Decision:
    decision_id: str
    book: str
    position: str                   # "flat" or "long"
    trigger: Trigger
    runs: list[RunRecord]
    action: str                     # BUY | SELL | HOLD; HOLD whenever the Decider failed or the Veto stopped it
    vetoed: bool
    new_signals: list[dict[str, Any]]
    rejected_signals: list[dict[str, Any]]

    @property
    def cost_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self.runs), Decimal(0))

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["runs"] = [r.to_json() for r in self.runs]
        d["cost_usd"] = str(self.cost_usd)
        return d


def _context(position: str, trigger: Trigger, now: datetime) -> str:
    return (f"Now (UTC): {now.astimezone(UTC).isoformat()}\nBook position: {position}\n"
            f"Trigger: {json.dumps(asdict(trigger), sort_keys=True)}")


def _validate_signals(raw: Sequence[Mapping[str, Any]], now_ms: int, active: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for s in raw:
        params, problems = parse_params(str(s.get("condition")), dict(s.get("params") or {}))
        hours = s.get("expires_in_hours")
        expires = now_ms + int(hours) * HOUR_MS if isinstance(hours, int) else now_ms
        problems += check_new(str(s.get("condition")), params, now_ms, expires, active + len(kept))
        item = {"condition": s.get("condition"), "params": {k: str(v) for k, v in params.items()},
                "expires_at_ms": expires, "why": s.get("why")}
        (rejected if problems else kept).append(item | ({"problems": problems} if problems else {}))
    return kept, rejected


def decide(book: str, position: str, trigger: Trigger, active_signals: Sequence[Signal], client: ModelClient, tools: Tools,
           budget: Budget, clock: Clock) -> Decision:
    """Run one decision end to end and return everything that happened; the caller stores it and hands BUY/SELL to the Guardian."""
    if position not in ("flat", "long"):
        raise ValueError("position must be flat or long")
    decision_id = str(uuid.uuid4())
    ctx = _context(position, trigger, clock())
    task = "Research your subject for this trigger and answer with the JSON report."
    with ThreadPoolExecutor(len(ANALYSTS)) as pool:
        futures = [pool.submit(run_role, a, f"{ctx}\n\n{task}", client, tools, budget, decision_id, clock) for a in ANALYSTS]
        runs = [f.result() for f in futures]
    reports = {r.role: r.output if r.outcome == "OK" else {"missing": r.outcome} for r in runs}
    active = [s for s in active_signals if s.status == "ACTIVE"]
    active_txt = json.dumps([{"condition": s.condition, "params": {k: str(v) for k, v in s.params.items()},
                              "expires_at_ms": s.expires_at_ms} for s in active], sort_keys=True)
    dec = run_role(DECIDER, f"{ctx}\nActive signals ({len(active)} of {MAX_ACTIVE}): {active_txt}\n\n"
                            f"Analyst reports:\n{json.dumps(reports, sort_keys=True)}\n\nDecide and answer with the JSON.",
                   client, tools, budget, decision_id, clock)
    runs.append(dec)
    action, vetoed = "HOLD", False
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if dec.outcome == "OK" and dec.output is not None:
        proposed = str(dec.output["action"]).upper()      # enum case is not guaranteed by structured outputs
        allowed = (position == "flat" and proposed == "BUY") or (position == "long" and proposed == "SELL")
        kept, rejected = _validate_signals(dec.output.get("new_signals") or [], int(clock().timestamp() * 1000), len(active))
        if allowed:
            veto = run_role(VETO, f"{ctx}\n\nAnalyst reports:\n{json.dumps(reports, sort_keys=True)}\n\n"
                                  f"Decision to check:\n{json.dumps(dec.output, sort_keys=True)}\n\nAnswer with the JSON.",
                            client, tools, budget, decision_id, clock)
            runs.append(veto)
            vetoed = veto.outcome != "OK" or veto.output is None or str(veto.output["verdict"]).upper() != "PASS"
            action = "HOLD" if vetoed else proposed
    return Decision(decision_id, book, position, trigger, runs, action, vetoed, kept, rejected)
