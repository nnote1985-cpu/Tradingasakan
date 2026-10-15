"""Instrument contracts for Spot, Futures and Options (BUILDPLAN v4.2 §8.2).

Phase 1 defines the shapes only. Which instrument types a build may trade is a
separate capability gate; today only SPOT and PERP_FUTURE are used, in paper
and research. Accounting differs by type: average cost applies to SPOT only;
futures settle by margin and PnL, options by
premium and expiry, and each gets its own method when enabled.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from trading.contracts.money import DecimalStr


class InstrumentType(StrEnum):
    SPOT = "SPOT"
    PERP_FUTURE = "PERP_FUTURE"
    DATED_FUTURE = "DATED_FUTURE"
    OPTION = "OPTION"


class OptionRight(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class CostBasisMethod(StrEnum):
    AVERAGE_COST = "AVERAGE_COST"          # spot holdings
    MARK_TO_MARKET = "MARK_TO_MARKET"      # futures: margin + realized/unrealized PnL
    PREMIUM = "PREMIUM"                    # options: premium paid/received, settle at expiry


COST_BASIS: dict[InstrumentType, CostBasisMethod] = {
    InstrumentType.SPOT: CostBasisMethod.AVERAGE_COST,
    InstrumentType.PERP_FUTURE: CostBasisMethod.MARK_TO_MARKET,
    InstrumentType.DATED_FUTURE: CostBasisMethod.MARK_TO_MARKET,
    InstrumentType.OPTION: CostBasisMethod.PREMIUM,
}

# Types this build's paper/research code handles. Others are contract-only.
ENABLED_INSTRUMENT_TYPES: frozenset[InstrumentType] = frozenset({InstrumentType.SPOT, InstrumentType.PERP_FUTURE})


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "instrument.v1"
    venue: str
    symbol: str
    type: InstrumentType
    base: str
    quote: str
    settle: str | None = None              # margin/settlement currency for derivatives
    contract_size: DecimalStr | None = None
    expiry: datetime | None = None
    strike: DecimalStr | None = None
    right: OptionRight | None = None

    @model_validator(mode="after")
    def _fields_match_type(self) -> Instrument:
        t = self.type
        derivative = t is not InstrumentType.SPOT
        if derivative and (self.settle is None or self.contract_size is None):
            raise ValueError(f"{t} needs settle and contract_size")
        if not derivative and (self.settle or self.contract_size is not None):
            raise ValueError("SPOT has no settle or contract_size")
        if (t in (InstrumentType.DATED_FUTURE, InstrumentType.OPTION)) != (self.expiry is not None):
            raise ValueError(f"{t}: expiry is required for dated futures and options only")
        if (t is InstrumentType.OPTION) != (self.strike is not None and self.right is not None):
            raise ValueError("strike and right are required for options only")
        if self.expiry is not None and self.expiry.tzinfo is None:
            raise ValueError("expiry must be timezone-aware")
        return self

    @property
    def cost_basis(self) -> CostBasisMethod:
        return COST_BASIS[self.type]

    @property
    def enabled_in_build(self) -> bool:
        return self.type in ENABLED_INSTRUMENT_TYPES
