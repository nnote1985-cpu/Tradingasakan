"""Durable loss limits and HALT latch (BUILDPLAN v4.2 §9.5, §9.6).

The day is the UTC day. Day-start trading and economic equity are the equity
carried into the first bar of that day. The high-water mark is on economic equity. Once HALT is
set it stays set across midnight and restarts; this module has no resume
(``automatic_resume_after_halt: false``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

import pandas as pd

from trading.risk.sizing import RiskProfile


@dataclass
class RiskState:
    day: str | None = None
    day_start_trading: Decimal | None = None
    day_start_economic: Decimal | None = None
    high_water_mark: Decimal | None = None
    halted: bool = False
    halt_reason: str | None = None
    halted_at: str | None = None

    def start_day(self, at: pd.Timestamp, trading_eq: Decimal, economic_eq: Decimal) -> bool:
        """Call before the first event of each bar with the equity carried in from
        the previous bar; on a new UTC day that equity becomes the day start."""
        day = str(pd.Timestamp(at).tz_convert("UTC").date())
        if day == self.day:
            return False
        self.day, self.day_start_trading, self.day_start_economic = day, trading_eq, economic_eq
        if self.high_water_mark is None:
            self.high_water_mark = economic_eq
        return True

    def observe(self, at: pd.Timestamp, trading_eq: Decimal, economic_eq: Decimal,
                profile: RiskProfile) -> str | None:
        """Record a mark. Returns the HALT reason when this mark trips it."""
        at = pd.Timestamp(at).tz_convert("UTC")
        if self.day is None:
            self.start_day(at, trading_eq, economic_eq)
        self.high_water_mark = economic_eq if self.high_water_mark is None else max(self.high_water_mark, economic_eq)
        if self.halted:
            return None
        reason = None
        lim = profile.daily_loss_trigger_fraction
        assert self.day_start_trading is not None and self.day_start_economic is not None
        if economic_eq <= 0:
            reason = "RUIN"
        elif self.day_start_trading - trading_eq >= self.day_start_trading * lim:
            reason = "DAILY_LOSS_TRADING"
        elif self.day_start_economic - economic_eq >= self.day_start_economic * lim:
            reason = "DAILY_LOSS_ECONOMIC"
        elif self.high_water_mark - economic_eq >= self.high_water_mark * profile.portfolio_drawdown_halt_fraction:
            reason = "DRAWDOWN"
        if reason:
            self.halted, self.halt_reason, self.halted_at = True, reason, at.isoformat()
        return reason

    def remaining_day_budget(self, trading_eq: Decimal, open_risk_to_stop: Decimal, profile: RiskProfile) -> Decimal:
        """max(0, day loss limit − loss so far − remaining loss-to-stop of open exposure)."""
        if self.halted or self.day_start_trading is None:
            return Decimal(0)
        limit = self.day_start_trading * profile.daily_loss_trigger_fraction
        loss = max(Decimal(0), self.day_start_trading - trading_eq)
        return max(Decimal(0), limit - loss - max(Decimal(0), open_risk_to_stop))

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict) -> RiskState:
        dec = ("day_start_trading", "day_start_economic", "high_water_mark")
        return cls(**{k: (Decimal(v) if k in dec and v is not None else v) for k, v in d.items()})
