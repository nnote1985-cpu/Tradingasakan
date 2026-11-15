"""The strategy contract (docs/m3-strategy-core-plan.md §3.1, platform plan A1).

A strategy reads closed market data and says what share of the book's equity it wants in one
instrument: a :class:`TargetDecision`. It knows nothing about the account, bid/ask, lot sizes or
orders; the order planner (``trading.execution.planner``) turns the target into orders.

``decide`` must be a pure function of its inputs, so a stored decision can be replayed: the same
inputs give the same weight, reason, snapshot and hash.

The contract allows negative weights because a perpetual can be short. Spot refuses them in the
planner, not here (A1).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

MAX_ABS_WEIGHT = Decimal(1)                 # 1x: never more than the book's equity, long or short


class Market(StrEnum):
    SPOT = "SPOT"
    PERP = "PERP"                           # USD-M perpetual, 1x isolated (platform plan §0.2)


def inputs_hash(inputs: Mapping[str, Any]) -> str:
    """sha256 of the snapshot as the Testnet bots have always stored it (``json.dumps(..., sort_keys=True)``)."""
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class TargetDecision:
    strategy_version: str
    instrument: str
    market: Market
    day: date                               # the UTC day whose close was decided on
    target_weight: Decimal | None           # None: an input was missing; the position is kept (NO_DECISION)
    reason: str
    inputs: Mapping[str, Any]
    inputs_sha256: str
    data_ready_at: datetime

    def __post_init__(self) -> None:
        if not self.strategy_version or not self.instrument:
            raise ValueError("strategy_version and instrument are required")
        if not isinstance(self.market, Market):
            raise ValueError(f"market must be a Market, not {self.market!r}")
        w = self.target_weight
        if w is not None:
            if not isinstance(w, Decimal) or not w.is_finite():
                raise ValueError("target_weight must be a finite Decimal or None")
            if abs(w) > MAX_ABS_WEIGHT:
                raise ValueError(f"target_weight {w} is outside [-1, 1]")
        if self.data_ready_at.tzinfo is None:
            raise ValueError("data_ready_at must be timezone-aware")
        if self.inputs_sha256 != inputs_hash(self.inputs):
            raise ValueError("inputs_sha256 does not match inputs")

    @classmethod
    def make(cls, *, inputs: Mapping[str, Any], **kw: Any) -> TargetDecision:
        """Build a decision with the hash computed from ``inputs``."""
        return cls(inputs=inputs, inputs_sha256=inputs_hash(inputs), **kw)


@dataclass(frozen=True)
class StrategyInputs:
    """Closed data a strategy decides from. ``bars`` and ``series`` are pandas objects (kept ``Any`` here)."""

    bars: Any                               # closed bars, indexed by open time (UTC)
    series: Mapping[str, Any] = field(default_factory=dict)   # e.g. daily funding and its estimate flags
    extra: Mapping[str, Any] = field(default_factory=dict)    # e.g. days filled from another source


@runtime_checkable
class Strategy(Protocol):
    version: str
    instrument: str
    market: Market

    def load(self, day: date) -> StrategyInputs:
        """Download the inputs for ``day`` (may block; never called on the tick thread)."""
        ...

    def decide(self, inputs: StrategyInputs, day: date, data_ready_at: datetime) -> TargetDecision | None:
        """The target at ``day``'s close, or None while the data does not reach ``day`` yet (wait and retry)."""
        ...
