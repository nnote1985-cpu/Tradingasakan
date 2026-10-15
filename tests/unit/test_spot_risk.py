"""Spot risk profile spot.v1 (chosen by Note 2026-10-15): alert at 5%/day or 15% drawdown, HALT at 25%."""

import json
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from tests.unit.test_spot_shadow import FLAT, frames, spot_book
from trading.risk.profiles import FUTURES_DEFAULT, PROFILES, SPOT_V1
from trading.risk.sizing import RiskProfile
from trading.shadow.risk_state import RiskState

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")


def test_profiles_registry_and_futures_default_unchanged():
    assert PROFILES["spot.v1"] is SPOT_V1 and FUTURES_DEFAULT == RiskProfile()
    assert FUTURES_DEFAULT.daily_loss_action == "HALT" and FUTURES_DEFAULT.drawdown_alert_fraction is None
    assert SPOT_V1.requested_exchange_leverage == 1 and SPOT_V1.max_gross_notional_to_equity == 1
    with pytest.raises(ValueError):
        RiskProfile(daily_loss_action="IGNORE")


def test_daily_loss_alerts_once_per_day_without_halting():
    rs = RiskState()
    rs.start_day(T0, Decimal(10000), Decimal(10000))
    assert rs.observe(T0, Decimal(9500), Decimal(9500), SPOT_V1) is None          # exactly 5%
    assert rs.observe(T0 + pd.Timedelta(hours=1), Decimal(9300), Decimal(9300), SPOT_V1) is None
    assert not rs.halted and [a["alert"] for a in rs.alerts] == ["DAILY_LOSS_TRADING"]
    nxt = T0 + pd.Timedelta(days=1)
    rs.start_day(nxt, Decimal(9300), Decimal(9300))
    assert rs.observe(nxt, Decimal(8830), Decimal(8830), SPOT_V1) is None          # 5.05% on the new day
    assert len(rs.alerts) == 2 and rs.alerts[1]["day_start_trading"] == "9300"


def test_daily_loss_on_economic_equity_alerts_too():
    rs = RiskState()
    rs.start_day(T0, Decimal(10000), Decimal(10000))
    rs.observe(T0, Decimal(9900), Decimal(9499), SPOT_V1)
    assert [a["alert"] for a in rs.alerts] == ["DAILY_LOSS_ECONOMIC"] and not rs.halted


def test_drawdown_alert_once_per_episode_and_rearms_on_new_high():
    rs = RiskState()
    for i, eq in enumerate([10000, 8500, 8400, 9000, 10100, 8580]):
        t = T0 + pd.Timedelta(days=i)
        rs.start_day(t, Decimal(eq), Decimal(eq))                 # one mark per day: no daily alerts
        assert rs.observe(t, Decimal(eq), Decimal(eq), SPOT_V1) is None
    assert [a["alert"] for a in rs.alerts] == ["DRAWDOWN", "DRAWDOWN"]
    assert rs.alerts[1]["high_water_mark"] == "10100"              # 8580 <= 10100 * 0.85 = 8585


def test_drawdown_25_percent_halts_and_survives_restart():
    rs = RiskState()
    for i, eq in enumerate([10000, 9000, 8000, 7500]):
        t = T0 + pd.Timedelta(days=i)
        rs.start_day(t, Decimal(eq), Decimal(eq))
        r = rs.observe(t, Decimal(eq), Decimal(eq), SPOT_V1)
    assert r == "DRAWDOWN" and rs.halted
    rs = RiskState.from_dict(json.loads(json.dumps(rs.to_dict())))
    t = T0 + pd.Timedelta(days=9)
    rs.start_day(t, Decimal(9000), Decimal(9000))
    assert rs.observe(t, Decimal(11000), Decimal(11000), SPOT_V1) is None and rs.halted


def test_ruin_always_halts_and_none_drawdown_never_halts():
    no_dd = replace(SPOT_V1, portfolio_drawdown_halt_fraction=None)
    rs = RiskState()
    for i, eq in enumerate([10000, 5000, 2000]):
        t = T0 + pd.Timedelta(days=i)
        rs.start_day(t, Decimal(eq), Decimal(eq))
        assert rs.observe(t, Decimal(eq), Decimal(eq), no_dd) is None
    t = T0 + pd.Timedelta(days=3)
    rs.start_day(t, Decimal(2000), Decimal(2000))
    assert rs.observe(t, Decimal(0), Decimal(0), no_dd) == "RUIN"


def test_old_state_without_alert_fields_still_loads():
    d = RiskState(day="2024-03-01", day_start_trading=Decimal(1), day_start_economic=Decimal(1),
                  high_water_mark=Decimal(1)).to_dict()
    for k in ("alerts", "daily_alert_day", "drawdown_alert_armed"):
        d.pop(k)
    rs = RiskState.from_dict(d)
    assert rs.alerts == [] and rs.drawdown_alert_armed


def test_spot_book_alerts_on_gap_loss_and_keeps_trading():
    full = replace(SPOT_V1, risk_per_trade_fraction=Decimal("1"), absolute_trade_risk_cap_usdt=Decimal("1e12"))
    gap = (54000, 54100, 53900, 54000)                     # opens 10% below, through the 2xATR stop
    flat2 = (54000, 54100, 53900, 54000)
    ex, h1, h4 = frames([FLAT] * 2 + [gap] + [flat2] * 26, {0: "LONG", 4: "LONG", 26: "LONG"})
    book = spot_book(profile=full)
    book.run(ex, h1, h4)
    events = [e["event"] for e in book.events]
    assert events.count("ALERT_DAILY_LOSS_TRADING") == 1 and not any(e.startswith("HALT") for e in events)
    assert not book.risk.halted and book.trades[0]["exit_reason"] == "STOP_GAP"
    # The day's loss budget still limits new risk that day; the next UTC day trading resumes.
    assert [c["status"] for c in book.candidates] == ["OPENED", "REJECT_NO_RISK_BUDGET", "OPENED"]
    assert book.cfg.describe()["profile"]["daily_loss_action"] == "ALERT"
