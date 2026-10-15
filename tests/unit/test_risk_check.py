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


def test_missing_funding_gives_no_suggestion():
    closes = list(np.linspace(100, 200, 110))
    d = _daily(closes)
    f = pd.Series(0.0003, index=d.index)
    f.iloc[-3] = np.nan                       # one funding day missing inside the 7-day window
    sig = signals(d, f)
    assert sig["funding_hot"].iloc[-1] is None
    assert np.isnan(sig["suggested_btc_weight"].iloc[-1])
    s = summary(sig, funding_estimated=False)
    assert s["suggested_btc_weight"] is None and not s["weight_changed"]
    assert "funding 7 วันไม่ครบ" in s["missing_inputs"]
    assert "ข้อมูลไม่พอ" in render_th(s)


def test_missing_price_day_blocks_sma_instead_of_skipping():
    d = _daily(list(np.linspace(100, 200, 110))).drop(D0 + pd.Timedelta(days=105))
    sig = signals(d, pd.Series(0.0003, index=d.index))
    assert len(sig) == 110                    # the gap stays as an empty row
    assert np.isnan(sig["suggested_btc_weight"].iloc[-1])


def test_clip_trims_monthly_file_to_requested_days():
    from datetime import date

    from trading.alerts.live_data import clip, missing_days
    idx = pd.date_range("2025-03-01", periods=31 * 24, freq="1h", tz="UTC")
    df = pd.DataFrame({"close": 1.0}, index=idx)
    out = clip(df, date(2025, 3, 5), date(2025, 3, 10))
    assert out.index.min() == pd.Timestamp("2025-03-05", tz="UTC")
    assert out.index.max() == pd.Timestamp("2025-03-10 23:00", tz="UTC")
    days = pd.date_range("2025-03-01", periods=5, freq="1D", tz="UTC").delete(2)
    assert missing_days(days, date(2025, 3, 1), date(2025, 3, 5)) == ["2025-03-03"]


def test_archive_checksum_mismatch_is_refused(monkeypatch):
    import hashlib

    from trading.alerts import live_data
    blob = b"zip-bytes"
    good = hashlib.sha256(blob).hexdigest().encode() + b"  f.zip"
    files = {"u": blob, "u.CHECKSUM": good}
    monkeypatch.setattr(live_data, "_get", lambda url: files.get(url))
    assert live_data._get_verified("u") == blob
    files["u.CHECKSUM"] = b"0" * 64 + b"  f.zip"
    with pytest.raises(live_data.ChecksumError):
        live_data._get_verified("u")
