"""Versioned, non-executable bot recipes. Never loaded by trading workers.

Drafts live in Supabase (``trading.bot_profile_drafts``), separate from the
executable YAML profiles. Each save appends a revision after an
expected-revision check; the (profile_id, revision) key refuses a second
writer of the same revision, so concurrent editors cannot silently overwrite
one another.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator

from trading.strategies.library import catalog
from trading.strategies.library.profile import BotProfile

Timeframe = Literal["1h", "4h", "1d"]


class StrategyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    number: StrictInt
    version: str
    timeframe: Timeframe
    params: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_catalog(self) -> StrategyRule:
        spec = catalog.get(self.number)
        if self.version != spec.version:
            raise ValueError("strategy version does not match the catalog")
        if self.timeframe not in spec.timeframes:
            raise ValueError("timeframe is not supported by this strategy")
        spec.resolve(self.params)
        return self


class StopLoss(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["atr", "fixed_percent", "trailing_atr"] = "atr"
    atr_period: int | None = Field(default=14, ge=2, le=300)
    atr_multiple: float | None = Field(default=2, gt=0, le=20, allow_inf_nan=False)
    percent: float | None = Field(default=None, gt=0, lt=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def required_fields(self) -> StopLoss:
        if self.kind == "fixed_percent":
            if self.percent is None or self.atr_period is not None or self.atr_multiple is not None:
                raise ValueError("fixed-percent stop requires percent only")
        elif self.atr_period is None or self.atr_multiple is None or self.percent is not None:
            raise ValueError("ATR stop requires period and multiple only")
        return self


class DraftProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    schema_version: Literal["bot-recipe-draft-v1"] = "bot-recipe-draft-v1"
    status: Literal["draft"] = "draft"
    name: str = Field(min_length=1, max_length=100)
    instrument: Literal["BTCUSDT_SPOT"] = "BTCUSDT_SPOT"
    timeframe: Timeframe = "4h"
    setup_mode: Literal["custom", "preset"] = "custom"
    preset_profile: BotProfile | None = None
    preset_strategy_versions: dict[int, str] = Field(default_factory=dict)
    trend_filter: StrategyRule | None = None
    entry: StrategyRule | None = None
    stop_loss: StopLoss = Field(default_factory=StopLoss)
    exit_kind: Literal["entry_signal", "strategy_signal"] = "entry_signal"
    exit_rule: StrategyRule | None = None
    risk_per_trade_percent: float = Field(default=0.5, gt=0, le=100, allow_inf_nan=False)
    max_exposure_percent: float = Field(default=25, gt=0, le=100, allow_inf_nan=False)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_recipe(self) -> DraftProfile:
        if self.setup_mode == "preset":
            if self.preset_profile is None or self.entry is not None or self.trend_filter is not None or self.exit_rule is not None:
                raise ValueError("preset mode requires a preset profile without custom signal rules")
            if self.exit_kind != "entry_signal" or self.timeframe != self.preset_profile.decision_clock:
                raise ValueError("preset uses its original decision clock and combined entry/exit signal")
            versions = {s.number: catalog.get(s.number).version for s in self.preset_profile.strategies}
            if self.preset_strategy_versions != versions:
                raise ValueError("preset strategy versions do not match the catalog")
        elif self.preset_profile is not None or self.preset_strategy_versions or self.entry is None:
            raise ValueError("custom mode requires entry and cannot contain preset settings")
        if self.entry is not None and self.entry.timeframe != self.timeframe:
            raise ValueError("entry timeframe must match the main timeframe")
        if self.trend_filter and catalog.get(self.trend_filter.number).family != "trend":
            raise ValueError("trend filter must use a trend strategy")
        if (self.exit_kind == "strategy_signal") != (self.exit_rule is not None):
            raise ValueError("exit rule is required only for strategy_signal")
        if self.risk_per_trade_percent > self.max_exposure_percent:
            raise ValueError("risk per trade cannot exceed max exposure")
        return self


class SaveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    request_id: UUID = Field(default_factory=uuid4)
    profile: DraftProfile


class DraftConflict(Exception):
    pass


class DraftsUnavailable(Exception):
    """No database, or the drafts migration has not been applied yet."""


def normalized(profile: DraftProfile) -> tuple[dict[str, Any], str]:
    """The body to store, with catalog defaults resolved so a saved recipe pins its values, and its sha256."""
    body = profile.model_dump(mode="json")
    for field in ("entry", "trend_filter", "exit_rule"):
        if body[field] is not None:
            body[field]["params"] = catalog.get(body[field]["number"]).resolve(body[field]["params"])
    if body["preset_profile"] is not None:
        for choice in body["preset_profile"]["strategies"]:
            spec = catalog.get(choice["number"])
            choice["params"] = spec.resolve(choice["params"])
            choice["timeframe"] = choice["timeframe"] or spec.default_timeframe
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return body, hashlib.sha256(encoded.encode()).hexdigest()


def draft_item(pid: Any, revision: int, body: dict[str, Any], at: datetime, author: Any, digest: str) -> dict[str, Any]:
    return {"profile_id": str(pid), "revision": revision, "profile": body, "saved_at": at.isoformat(),
            "saved_by": str(author), "hash": digest, "execution_supported": False}


class DraftStore(Protocol):
    async def list_latest(self) -> list[dict[str, Any]]: ...
    async def save(self, request: SaveDraft, author: UUID) -> dict[str, Any]: ...


class NoDrafts:
    async def list_latest(self) -> list[dict[str, Any]]:
        raise DraftsUnavailable

    async def save(self, request: SaveDraft, author: UUID) -> dict[str, Any]:
        raise DraftsUnavailable


COLS = "profile_id, revision, body, saved_at, saved_by, digest"
LATEST_SQL = f"""
select {COLS} from (
  select distinct on (profile_id) {COLS} from trading.bot_profile_drafts order by profile_id, revision desc
) d order by saved_at desc, profile_id
"""


class PgDraftStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def list_latest(self) -> list[dict[str, Any]]:
        try:
            async with self.pool.connection() as conn:
                rows = await (await conn.execute(LATEST_SQL)).fetchall()
        except pg_errors.UndefinedTable as e:
            raise DraftsUnavailable from e
        return [draft_item(*r) for r in rows]

    async def save(self, request: SaveDraft, author: UUID) -> dict[str, Any]:
        body, digest = normalized(request.profile)
        pid = request.profile_id or uuid4()
        try:
            async with self.pool.connection() as conn, conn.transaction():
                previous = await (await conn.execute(f"select {COLS} from trading.bot_profile_drafts where request_id = %s",
                                                     (request.request_id,))).fetchone()
                if previous is not None:
                    if previous[5] != digest or previous[4] != author or (request.profile_id and previous[0] != request.profile_id):
                        raise DraftConflict("request id already used with a different payload")
                    return draft_item(*previous)
                current = (await (await conn.execute("select coalesce(max(revision), 0) from trading.bot_profile_drafts "
                                                     "where profile_id = %s", (pid,))).fetchone())[0]   # type: ignore[index]
                if current != request.expected_revision or (request.profile_id is not None and current == 0):
                    raise DraftConflict("draft changed or no longer exists; reload before saving")
                row = await (await conn.execute(
                    f"insert into trading.bot_profile_drafts (profile_id, revision, body, digest, saved_by, request_id) "
                    f"values (%s, %s, %s, %s, %s, %s) returning {COLS}",
                    (pid, current + 1, Jsonb(body), digest, author, request.request_id))).fetchone()
        except pg_errors.UniqueViolation as e:          # another editor wrote this revision first
            raise DraftConflict("draft changed; reload before saving") from e
        except pg_errors.UndefinedTable as e:
            raise DraftsUnavailable from e
        assert row is not None
        return draft_item(*row)
