"""Bot profiles: which numbered strategies a bot uses, on which timeframe, with which params.

A profile is a YAML file in ``configs/profiles/``. Changing anything in it means a
new ``version`` (and, once a profile runs a paper book, a new book); the profile
hash makes a silent edit visible. Profiles cannot raise risk: ``risk_profile``
must name an existing risk profile and the output is a 1x long/flat BTC weight.

Combining several strategies into one daily weight:

- ``average``: the mean of the strategies' weights, rounded down to 0, 0.5 or 1.
- ``main_with_filters``: the first strategy's weight, or 0 when any other
  strategy's weight is 0.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.risk.profiles import PROFILES
from trading.strategies.library import catalog
from trading.strategies.library.bars import resample

PROFILE_DIR = Path(__file__).resolve().parents[5] / "configs" / "profiles"


class StrategyChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    number: int
    timeframe: str | None = None
    params: dict[str, int | float] = Field(default_factory=dict)


class BotProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    version: int = Field(ge=1)
    name: str
    status: Literal["draft", "active", "retired"] = "draft"
    instrument: Literal["BTCUSDT_SPOT"] = "BTCUSDT_SPOT"
    decision_clock: Literal["1d"] = "1d"
    combine: Literal["average", "main_with_filters"] = "average"
    strategies: list[StrategyChoice] = Field(min_length=1, max_length=20)
    risk_profile: str = "spot.v1"
    ai_mode: Literal["off"] = "off"
    notes: str = ""

    @field_validator("risk_profile")
    @classmethod
    def _risk_known(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"unknown risk profile {v!r}; known: {sorted(PROFILES)}")
        if v != "spot.v1":
            raise ValueError("BTC spot profiles must use spot.v1")
        return v

    @model_validator(mode="after")
    def _strategies_valid(self) -> BotProfile:
        nos = [s.number for s in self.strategies]
        if len(set(nos)) != len(nos):
            raise ValueError("a strategy number appears twice")
        for s in self.strategies:
            spec = catalog.get(s.number)
            tf = s.timeframe or spec.default_timeframe
            if tf not in spec.timeframes:
                raise ValueError(f"strategy {s.number} does not run on {tf}")
            spec.resolve(s.params)
        return self

    def resolved(self) -> list[dict[str, Any]]:
        """Each choice with its template, timeframe and full params (defaults filled in)."""
        out = []
        for s in self.strategies:
            spec = catalog.get(s.number)
            out.append({"number": s.number, "template_id": spec.template_id, "version": spec.version, "name": spec.name,
                        "timeframe": s.timeframe or spec.default_timeframe, "params": spec.resolve(s.params)})
        return out

    def profile_hash(self) -> str:
        """Hash of what the bot does (name, notes and status left out; params with defaults filled in)."""
        body = self.model_dump(mode="json", include={"profile_id", "version", "instrument", "decision_clock", "combine",
                                                     "risk_profile", "ai_mode"}) | {"strategies": self.resolved()}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(path: str | Path) -> BotProfile:
    return BotProfile.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_all(directory: str | Path = PROFILE_DIR) -> list[BotProfile]:
    profiles = [load(p) for p in sorted(Path(directory).glob("*.yaml"))]
    keys = [(p.profile_id, p.version) for p in profiles]
    if len(set(keys)) != len(keys):
        raise ValueError("two profile files share a profile_id and version")
    return profiles


def combine(weights: list[float], how: str) -> float:
    if how == "average":
        return math.floor(sum(weights) / len(weights) * 2 + 1e-9) / 2
    if how == "main_with_filters":
        return 0.0 if any(w == 0 for w in weights[1:]) else weights[0]
    raise ValueError(f"unknown combine rule {how!r}")


def daily_weights(profile: BotProfile, h1: pd.DataFrame) -> pd.DataFrame:
    """Per-strategy and combined target weight at each UTC daily close.

    Index: the daily close time (00:00 UTC of the next day). A strategy's weight
    there is its weight at its last bar that closed at or before that time.
    """
    day_close = pd.date_range(h1.index.min().floor("1D") + pd.Timedelta(days=1), h1.index.max().floor("1D") + pd.Timedelta(days=1),
                              freq="1D", tz="UTC", name="decided_at")
    cols = {}
    for r in profile.resolved():
        w = catalog.get(r["number"]).signal(resample(h1, r["timeframe"]), r["params"], r["timeframe"])
        cols[f"s{r['number']}"] = w.reindex(w.index.union(day_close)).ffill().reindex(day_close).fillna(0.0)
    out = pd.DataFrame(cols)
    out["target_weight"] = [combine(list(row), profile.combine) for row in out.to_numpy()]
    return out
