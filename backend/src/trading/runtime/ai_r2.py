"""tn-r2-v1: the Phase 5 r2 AI proposer trading its own Testnet account (docs/testnet-p6-plan.md).

Once per UTC day, after the same inputs as tn-r10a-code-v1 are out, the model proposes the
book's BTC weight (0, 0.5 or 1) with the r2 prompt, schema, model and graph unchanged
(``trading.analysis.pathA_proposer``). The snapshot has the same keys as the Shadow book
pathA-p5-r2: close, SMA100, trend, 7-day funding, the funding window, the last 30 closes, and
the book's own weight and drawdown; Code's suggested weight is never shown (RT13). R10a's
weight is stored beside the proposal for the report only.

Code compiles the answer; the model has no tools, never sees the exchange and sizes nothing
(RT15). A failure (invalid, late, refusal, provider error, budget cap) keeps the current target
and is stored with its own category (RT12). Before the book has any target, a failure leaves it
undecided (NO_DECISION). Orders, retries and Guardian are exactly those of the R10a bot.

Spend is capped in code at ``MONTHLY_CAP_USD`` per calendar month (UTC), summed from the stored
proposals; a call is not made when less than the graph's ``max_run_cost_usd`` is left.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from trading.analysis import pathA_filter as pf
from trading.analysis import pathA_proposer as pp
from trading.contracts.analysis import AgentGraphConfig
from trading.runtime.r10a import ALLOCATION, Decision, R10aBot, decide, load_inputs

BOOK = "tn-r2-v1"
RULES_VERSION = "tn-r2-v1"
EXPERIMENT_ID = "tn-r2-v1"
ARM_ID = "tn-r2-v1.ai"
KEY_ENV = "TN_ANTHROPIC_KEY"           # its own key and spend limit, not the Shadow books' P5_ANTHROPIC_KEY
MONTHLY_CAP_USD = Decimal("5")
# snapshot key -> signals column, as scripts/paper_trade.py decision_inputs (pathA-p5-r2 sees the same keys)
SNAPSHOT_KEYS = {"close_perp": "close", "sma100": "sma100", "trend_up": "trend_up",
                 "funding_7d_per_day": "funding_7d_per_day", "funding_hot": "funding_hot"}


def _val(x: Any) -> Any:
    if x is None or (isinstance(x, float) and x != x):
        return None
    return bool(x) if isinstance(x, (bool, np.bool_)) else float(x)


def snapshot_inputs(daily: pd.DataFrame, funding: pd.Series, code: Decision) -> tuple[dict[str, Any], list[tuple[str, float]]]:
    """The r2 snapshot inputs for ``code.day`` (same keys as pathA-p5-r2) and the last 30 closes."""
    from trading.alerts.risk_check import signals

    sig = signals(daily, funding)
    sig = sig[sig.index <= pd.Timestamp(code.day, tz="UTC")]
    row = sig.iloc[-1]
    inputs = {k: _val(row[c]) for k, c in SNAPSHOT_KEYS.items()}
    inputs["funding_window"] = code.inputs["funding_window"]
    closes = [(str(d.date()), float(c)) for d, c in daily["close"][daily.index <= sig.index[-1]].iloc[-30:].items()]
    return inputs, closes


def propose_decision(day: date, current: Decimal | None, drawdown: float, budget_left: Decimal,
                     provider: pf.Provider, graph: AgentGraphConfig,
                     load: Callable[[date], tuple[pd.DataFrame, pd.Series, pd.Series, list[str]]] = load_inputs,
                     clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Decision | None:
    """R10a's inputs for ``day``, then one r2 proposal. None while the inputs are not out (as R10a)."""
    daily, funding, est, filled = load(day)
    ready = clock()
    code = decide(daily, funding, est, day, ready, filled)
    if code is None:
        return None
    if code.weight is None:                    # inputs missing: no question is worth paying for
        return Decision(day, None, code.notes, {"code": code.inputs}, code.inputs_sha256, ready)
    inputs, closes = snapshot_inputs(daily, funding, code)
    own = float(current) if current is not None else 0.0
    snap = pp.snapshot(str(day), inputs, closes, own, drawdown)
    rec = pp.propose(f"{EXPERIMENT_ID}:{day}", snap, own, float(code.weight), provider, graph, budget_left, clock,
                     experiment_id=EXPERIMENT_ID, arm_id=ARM_ID)
    out = rec["outcome"]
    if out["accepted"]:
        weight: Decimal | None = Decimal(str(out["target_weight"]))
        reason = (rec["response"] or {}).get("reason", "")
        notes = f"AI {out['reason_code']}: {reason}"[:400]
    else:
        weight = current
        notes = f"AI did not answer ({out['error_category']}); " + ("target kept" if current is not None else "no target yet")
    notes += f"; R10a {code.weight:g}"
    body = {"code": code.inputs, "code_weight": str(code.weight), "snapshot": snap, "proposal": rec}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return Decision(day, weight, notes, body, sha, ready)


@dataclass
class AiR2Bot(R10aBot):
    """The R10a bot with the r2 proposer deciding the weight; set ``provider`` and ``graph``."""

    provider: pf.Provider | None = None
    graph: AgentGraphConfig = field(default_factory=pp.load_proposer_graph)
    monthly_cap: Decimal = MONTHLY_CAP_USD
    load: Callable[[date], tuple[pd.DataFrame, pd.Series, pd.Series, list[str]]] = load_inputs

    def __post_init__(self) -> None:
        self.book, self.rules_version, self.allocation = BOOK, RULES_VERSION, ALLOCATION
        self.label, self.tag = "AI r2", "air2"
        if self.provider is None:
            raise ValueError("AiR2Bot needs a provider")

    def budget_left(self, now: datetime) -> Decimal:
        month = now.date().replace(day=1)
        return self.monthly_cap - self.store.ai_spend(self.book, month)

    def _submit(self, day: date) -> Future[Decision | None]:
        # Read on the tick thread: the store's connection is not shared with the download thread.
        current = self.store.last_weight(self.book, day)
        hwm, equity = self.store.book_equity(self.book)
        drawdown = max(0.0, 1 - float(equity) / float(hwm)) if hwm and equity is not None else 0.0
        left = self.budget_left(datetime.now(UTC))
        assert self.provider is not None
        return self.pool.submit(propose_decision, day, current, drawdown, left, self.provider, self.graph, self.load)
