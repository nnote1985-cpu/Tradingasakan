"""Bot configuration ``bot-config-v1``: shape, server-side validation and hash (m4-bot-config-plan.md §3.1, C2).

The API, the database check and the worker all use :func:`normalize` so the hash a person approved
is the hash the worker recomputes before it trades. Validation returns errors per field
(``{"field", "code", "message_th"}``) so the web can show each one under its input.

What is refused here, whatever the caller: LIVE, any venue but a Testnet, leverage other than 1x,
margin other than isolated, unknown fields, a template or version not in the registry, a timeframe
the template does not run on, params outside their bounds, an allocation outside the template's
bounds. Checks that need the account (free balance, one bot per account) are in
:func:`approval_errors` and in the API.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading.bots import templates
from trading.contracts.strategy import Market

SCHEMA = "bot-config-v1"
VENUE = {Market.SPOT: "SPOT_TESTNET", Market.PERP: "USDM_TESTNET"}
MAINTENANCE_MARGIN_RATE = Decimal("0.004")      # BTCUSDT perp, lowest tier
BALANCE_MAX_AGE = timedelta(minutes=15)
ACCOUNT_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"      # the worker's WORKER_ACCOUNT rule


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    market: Market


class StrategyChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    template: str
    version: str
    timeframe: str
    params: dict[str, Any] = Field(default_factory=dict)


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset: Literal["USDT"] = "USDT"
    amount: str = Field(pattern=r"^[0-9]{1,9}(\.[0-9]{1,2})?$")


class FuturesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    leverage: int
    margin_mode: str
    stop_percent: str = Field(pattern=r"^[0-9]{1,2}(\.[0-9]{1,2})?$")


class SourceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: UUID
    revision: int = Field(ge=1)


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    schema_version: Literal["bot-config-v1"] = "bot-config-v1"
    name: str = Field(min_length=1, max_length=100)
    account: str = Field(pattern=ACCOUNT_PATTERN)
    venue: str
    instrument: Instrument
    strategy: StrategyChoice
    allocation: Allocation
    risk_profile: Literal["spot.tn.v1"] = "spot.tn.v1"
    futures: FuturesSettings | None = None
    mode: str = "SHADOW"
    source_draft: SourceDraft | None = None
    notes: str = Field(default="", max_length=2000)


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message_th: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message_th": self.message_th}


class ConfigInvalid(ValueError):
    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("; ".join(f"{e.field}: {e.code}" for e in errors))
        self.errors = errors


PYDANTIC_TH = {"missing": "ต้องกรอก", "extra_forbidden": "ไม่รู้จักช่องนี้", "string_pattern_mismatch": "รูปแบบไม่ถูกต้อง",
               "literal_error": "ค่านี้ใช้ไม่ได้", "enum": "ค่านี้ใช้ไม่ได้", "string_too_short": "ต้องกรอก",
               "string_too_long": "ยาวเกินไป"}


def liquidation_distance_percent(leverage: int) -> Decimal:
    """How far price can move against an isolated position before liquidation, in percent of entry."""
    return (Decimal(1) / leverage - MAINTENANCE_MARGIN_RATE) * 100


def _semantic(c: BotConfig) -> list[FieldError]:
    errs: list[FieldError] = []
    if c.mode == "LIVE":
        errs.append(FieldError("mode", "live_refused", "LIVE ปิดอยู่ บอทรันได้แค่ SHADOW หรือ TESTNET"))
    elif c.mode not in ("SHADOW", "TESTNET"):
        errs.append(FieldError("mode", "invalid", "mode ต้องเป็น SHADOW หรือ TESTNET"))
    if c.venue != VENUE[c.instrument.market]:
        errs.append(FieldError("venue", "invalid", f"ตลาด {c.instrument.market.value} ต้องใช้ {VENUE[c.instrument.market]}"))
    if c.instrument.market is Market.SPOT and c.futures is not None:
        errs.append(FieldError("futures", "not_allowed", "Spot ไม่มีค่าของ Futures"))
    if c.instrument.market is Market.PERP:
        f = c.futures
        if f is None:
            errs.append(FieldError("futures", "missing", "Perp ต้องตั้ง leverage, margin และระยะ stop"))
        else:
            if f.leverage != 1:
                errs.append(FieldError("futures.leverage", "leverage_fixed", "leverage ต้องเป็น 1x เท่านั้น"))
            if f.margin_mode != "isolated":
                errs.append(FieldError("futures.margin_mode", "isolated_only", "margin ต้องเป็น isolated เท่านั้น"))
            stop = Decimal(f.stop_percent)
            liq = liquidation_distance_percent(1)
            if not Decimal(0) < stop < liq:
                errs.append(FieldError("futures.stop_percent", "stop_beyond_liquidation",
                                       f"ระยะ stop ต้องมากกว่า 0 และน้อยกว่าระยะ liquidation ที่ 1x ({liq:.1f}%)"))
    t = templates.get(c.strategy.template)
    if t is None:
        errs.append(FieldError("strategy.template", "unknown_template", "ไม่มีกลยุทธ์นี้ในรายการที่รันได้"))
        return errs
    if c.strategy.version != t.version:
        errs.append(FieldError("strategy.version", "version_mismatch", f"version ต้องเป็น {t.version}"))
    if c.instrument.market is not t.market or c.instrument.symbol != t.symbol:
        errs.append(FieldError("instrument", "template_market", f"กลยุทธ์นี้รันบน {t.symbol} {t.market.value} เท่านั้น"))
    if c.strategy.timeframe not in t.timeframes:
        errs.append(FieldError("strategy.timeframe", "timeframe_unsupported",
                               f"กลยุทธ์นี้ใช้ timeframe {', '.join(t.timeframes)} เท่านั้น"))
    for k in sorted(set(c.strategy.params) - set(t.params)):
        errs.append(FieldError(f"strategy.params.{k}", "unknown_param", "กลยุทธ์นี้ไม่มีค่านี้"))
    for k, p in t.params.items():
        if k in c.strategy.params and (why := p.error(c.strategy.params[k])):
            errs.append(FieldError(f"strategy.params.{k}", "out_of_bounds", why))
    amount = Decimal(c.allocation.amount)
    if not t.min_allocation <= amount <= t.max_allocation:
        errs.append(FieldError("allocation.amount", "allocation_bounds",
                               f"เงินของบอทต้องอยู่ระหว่าง {t.min_allocation:,} ถึง {t.max_allocation:,} USDT"))
    return errs


def parse(payload: Any) -> BotConfig:
    """A checked config, or :class:`ConfigInvalid` with every problem found."""
    try:
        c = BotConfig.model_validate(payload)
    except ValidationError as e:
        errs = []
        for x in e.errors():
            path = ".".join(str(p) for p in x["loc"] if not isinstance(p, int)) or "body"
            errs.append(FieldError(path, x["type"], PYDANTIC_TH.get(x["type"], x["msg"])))
        raise ConfigInvalid(errs) from e
    errs = _semantic(c)
    if errs:
        raise ConfigInvalid(errs)
    return c


def normalize(c: BotConfig) -> tuple[dict[str, Any], str]:
    """The body to store (template defaults filled in, so a revision pins every value) and its sha256."""
    body = c.model_dump(mode="json")
    t = templates.get(c.strategy.template)
    if t is not None:
        body["strategy"]["params"] = t.resolve(c.strategy.params)
    body["allocation"]["amount"] = str(Decimal(c.allocation.amount).quantize(Decimal("0.01")))
    return body, body_hash(body)


def body_hash(body: dict[str, Any]) -> str:
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def book_for(bot_id: UUID) -> str:
    """The book a bot trades under; the same for every revision so its position carries over."""
    return f"bot-{bot_id.hex[:8]}"


@dataclass(frozen=True)
class AccountFunds:
    """The account's last reconciled USDT, as the worker stored it (``trading.exec_balances``)."""
    usdt_free: Decimal | None
    as_of: datetime | None
    verdict: str | None             # verdict of the reconcile that produced it


def approval_errors(c: BotConfig, funds: AccountFunds, now: datetime) -> list[FieldError]:
    """C2 checks against the account at approve time."""
    if c.instrument.market is Market.PERP:
        return [FieldError("instrument", "perp_not_executable", "ยังไม่มีกลยุทธ์ Perp ที่รันได้ (Futures พักไว้ตามแผน)")]
    if funds.usdt_free is None or funds.as_of is None:
        return [FieldError("allocation.amount", "no_balance", f"ยังไม่มียอดเงินของบัญชี {c.account} จาก worker")]
    if funds.verdict != "MATCH" or now - funds.as_of > BALANCE_MAX_AGE:
        return [FieldError("allocation.amount", "balance_stale",
                           f"ยอดเงินล่าสุดของ {c.account} เก่ากว่า 15 นาทีหรือ reconcile ไม่ MATCH รอ worker ตรวจบัญชีใหม่")]
    try:
        amount = Decimal(c.allocation.amount)
    except InvalidOperation:
        return [FieldError("allocation.amount", "invalid", "จำนวนเงินไม่ถูกต้อง")]
    if amount > funds.usdt_free:
        return [FieldError("allocation.amount", "over_free_balance",
                           f"เงินของบอท {amount:,} USDT มากกว่า USDT ที่ว่างในบัญชี ({funds.usdt_free:,.2f})")]
    return []
