import numpy as np
import pandas as pd
import pytest

from trading.alerts.live_data import estimate_funding
from trading.alerts.risk_check import events, render_th, signals, summary

D0 = pd.Timestamp("2025-01-01", tz="UTC")


def _daily(closes):
    idx = pd.date_range(D0, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_trend_break_and_weight_change_are_reported():
    closes = [100.0] * 100 + [110.0, 90.0]
    d = _daily(closes)
    sig = signals(d, pd.Series(0.0003, index=d.index))
    assert bool(sig["trend_up"].iloc[-2]) and not bool(sig["trend_up"].iloc[-1])
    s = summary(sig, funding_estimated=False)
    assert s["suggested_btc_weight"] == 0.0 and s["weight_changed"]
    assert "ราคาปิดหลุดค่าเฉลี่ย 100 วัน" in s["events"]
    assert "ใต้ค่าเฉลี่ย" in render_th(s)


def test_hot_funding_halves_weight():
    closes = list(np.linspace(100, 200, 110))
    d = _daily(closes)
    f = pd.Series(0.0003, index=d.index)
    f.iloc[-1] = 0.005          # pushes the 7-day mean over 0.0009 on the last day only
    sig = signals(d, f)
    assert bool(sig["funding_hot"].iloc[-1])
    assert sig["suggested_btc_weight"].iloc[-1] == 0.5
    assert "funding เฉลี่ย 7 วันสูงเกิน 0.03%/8 ชม. (ตลาดใช้เลเวอเรจหนัก)" in events(sig)


def test_funding_estimate_uses_clamp_around_interest():
    idx = pd.date_range(D0, periods=16, freq="1h", tz="UTC")
    prem = pd.Series([0.0002] * 8 + [0.0012] * 8, index=idx)
    est = estimate_funding(prem)
    # small premium → interest-dominated 0.01%; large premium → P - 0.05%.
    assert est.loc[D0 + pd.Timedelta(hours=8)] == pytest.approx(0.0001)
    assert est.loc[D0 + pd.Timedelta(hours=16)] == pytest.approx(0.0007)
