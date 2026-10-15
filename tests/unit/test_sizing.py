from decimal import Decimal

from trading.risk.sizing import (
    InstrumentRules,
    RiskProfile,
    SizingAllowances,
    plan_entry,
    round_stop,
    round_take_profit,
    size_linear_position,
)


def test_T06_sizing_example():
    out = size_linear_position(budget=50, entry=60000, stop=59400, entry_fee="0.0005",
                               exit_fee="0.0005", stop_slip=60, funding_reserve=0,
                               qty_step="0.001", qty_cap=1000)
    assert out["qty"] == Decimal("0.069")
    assert out["estimated_risk"] == Decimal("49.6593")


def test_T07_below_min_notional_is_rejected_not_rounded_up():
    plan = plan_entry(side="LONG", entry_estimate=60000, atr=300, stop_atr_mult=2,
                      equity=100, remaining_day_budget=1000, profile=RiskProfile(),
                      rules=InstrumentRules(), allow=SizingAllowances())
    assert plan == {"reject": "REJECT_MIN_NOTIONAL"}


def test_stop_and_tp_rounding_direction():
    tick = Decimal("0.1")
    assert round_stop(Decimal("59419.97"), tick, "LONG") == Decimal("59420.0")
    assert round_stop(Decimal("60580.03"), tick, "SHORT") == Decimal("60580.0")
    assert round_take_profit(Decimal("61220.07"), tick, "LONG") == Decimal("61220.0")
    assert round_take_profit(Decimal("58779.93"), tick, "SHORT") == Decimal("58780.0")


def test_absolute_cap_limits_budget_when_equity_grows():
    plan = plan_entry(side="LONG", entry_estimate=60000, atr=300, stop_atr_mult=2,
                      equity=50_000, remaining_day_budget=10_000, profile=RiskProfile(),
                      rules=InstrumentRules(), allow=SizingAllowances())
    assert plan["budget"] == Decimal("50")
    assert plan["estimated_risk"] <= Decimal("50")


def test_remaining_day_budget_caps_risk():
    plan = plan_entry(side="SHORT", entry_estimate=60000, atr=300, stop_atr_mult=2,
                      equity=10_000, remaining_day_budget=20, profile=RiskProfile(),
                      rules=InstrumentRules(), allow=SizingAllowances())
    assert plan["budget"] == Decimal("20")
    assert plan["stop"] == Decimal("60600.0")
