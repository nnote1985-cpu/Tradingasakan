"""Strategy version identity (§20.1 strategy_versions, §18)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrategyVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")   # e.g. pathA-v1
    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    frozen_ref: str | None = None                                     # e.g. frozen/pathA-v1
    config_hash: str | None = None
    parent_label: str | None = None
    artifact_pointer: str | None = None
