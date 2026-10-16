"""Durable loss limits and HALT latch (BUILDPLAN v4.2 §9.5, §9.6).

The day is the UTC day. Day-start trading and economic equity are the equity
carried into the first bar of that day. The high-water mark is on economic equity. Once HALT is
set it stays set across midnight and restarts; this module has no resume
(``automatic_resume_after_halt: false``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    alerts: list = field(default_factory=list)          # ALERT records: they never stop trading
    daily_alert_day: str | None = None                  # one daily-loss alert per UTC day
    drawdown_alert_armed: bool = True                   # re-armed when a new high-water mark is set

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
        """Record a mark. Returns the HALT reason when this mark trips it.

        With ``daily_loss_action == "ALERT"`` a daily-loss breach is appended to
        ``alerts`` (once per UTC day) instead of halting. ``drawdown_alert_fraction``
        adds one alert per drawdown episode. A ``None`` drawdown halt never halts.
        RUIN always halts."""
        at = pd.Timestamp(at).tz_convert("UTC")
        if self.day is None:
            self.start_day(at, trading_eq, economic_eq)
        if self.high_water_mark is None or economic_eq > self.high_water_mark:
            self.high_water_mark = economic_eq
            self.drawdown_alert_armed = True
        if self.halted:
            return None
        lim = profile.daily_loss_trigger_fraction
        assert self.day_start_trading is not None and self.day_start_economic is not None
        daily = None
        if self.day_start_trading - trading_eq >= self.day_start_trading * lim:
            daily = "DAILY_LOSS_TRADING"
        elif self.day_start_economic - economic_eq >= self.day_start_economic * lim:
            daily = "DAILY_LOSS_ECONOMIC"
        dd = self.high_water_mark - economic_eq
        dd_halt = profile.portfolio_drawdown_halt_fraction
        reason = None
        if economic_eq <= 0:
            reason = "RUIN"
        elif daily and profile.daily_loss_action == "HALT":
            reason = daily
        elif dd_halt is not None and dd >= self.high_water_mark * dd_halt:
            reason = "DRAWDOWN"
        if reason:
            self.halted, self.halt_reason, self.halted_at = True, reason, at.isoformat()
            return reason
        if daily and self.daily_alert_day != self.day:
            self.daily_alert_day = self.day
            self._alert(at, daily, trading_eq, economic_eq)
        ddal = profile.drawdown_alert_fraction
        if ddal is not None and self.drawdown_alert_armed and dd >= self.high_water_mark * ddal:
            self.drawdown_alert_armed = False
            self._alert(at, "DRAWDOWN", trading_eq, economic_eq)
        return None

    def _alert(self, at: pd.Timestamp, reason: str, trading_eq: Decimal, economic_eq: Decimal) -> None:
        self.alerts.append({"time": at.isoformat(), "alert": reason, "trading_equity": str(trading_eq),
                            "economic_equity": str(economic_eq), "high_water_mark": str(self.high_water_mark),
                            "day_start_trading": str(self.day_start_trading),
                            "day_start_economic": str(self.day_start_economic)})

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
