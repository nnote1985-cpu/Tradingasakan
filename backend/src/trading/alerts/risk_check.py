"""Daily BTC risk check for path A (BTC core, alerts only, a person decides).

Signals come from the rules tested in docs/research (v2 trend state, v3
funding overlay). Neither beat BTC buy & hold in the 2024 bull year; they are
alerts with a known cost, not a market-beating system.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

SMA_DAYS = 100
FUNDING_WINDOW = 7
FUNDING_THRESHOLD_PER_DAY = 0.0009     # 0.03% per 8h
VOL_DAYS = 30
HIGH_VOL = 0.80                        # annualised; BTC's long-run level is roughly 0.5-0.7
DRAWDOWN_WARN = 0.20


def _known(flag: pd.Series, inputs: pd.Series) -> pd.Series:
    """Boolean signal as object dtype: True/False where the input exists, None where it is missing."""
    return flag.astype(object).where(inputs.notna(), None)


def signals(daily: pd.DataFrame, funding_daily: pd.Series, oi: pd.Series | None = None) -> pd.DataFrame:
    """One row per UTC day with every signal known at that day's close.

    Missing days are kept as empty rows, so any window that spans a gap (or
    lacks funding) yields None/NaN, and no weight is suggested from it.
    """
    days = pd.date_range(daily.index.min(), daily.index.max(), freq="1D")
    c = daily["close"].astype(float).reindex(days)
    out = pd.DataFrame(index=days)
    out["close"] = c
    out["sma100"] = c.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    out["trend_up"] = _known(c > out["sma100"], out["sma100"])
    out["dist_to_sma"] = c / out["sma100"] - 1
    f = funding_daily.reindex(days)
    out["funding_7d_per_day"] = f.rolling(FUNDING_WINDOW, min_periods=FUNDING_WINDOW).mean()
    out["funding_hot"] = _known(out["funding_7d_per_day"] > FUNDING_THRESHOLD_PER_DAY, out["funding_7d_per_day"])
    r = np.log(c / c.shift(1))
    out["vol_30d"] = r.rolling(VOL_DAYS, min_periods=VOL_DAYS).std() * math.sqrt(365)
    out["high_vol"] = _known(out["vol_30d"] > HIGH_VOL, out["vol_30d"])
    out["drawdown_365d"] = 1 - c / c.rolling(365, min_periods=1).max()
    if oi is not None and len(oi):
        o = oi.reindex(days)
        out["oi_usdt"] = o
        out["oi_7d_change"] = o / o.shift(7) - 1
    trend = out["trend_up"].map({True: 1.0, False: 0.0})
    funding = out["funding_hot"].map({True: 0.5, False: 1.0})
    out["suggested_btc_weight"] = (trend * funding).astype(float)   # NaN = not enough data
    return out


def missing_inputs(sig: pd.DataFrame) -> list[str]:
    """Thai labels for the inputs that block today's suggestion."""
    t = sig.iloc[-1]
    out = []
    if pd.isna(t["close"]):
        out.append("ไม่มีราคาปิดของวันล่าสุด")
    elif pd.isna(t["sma100"]):
        out.append("ราคาย้อนหลัง 100 วันไม่ครบ")
    if pd.isna(t["funding_7d_per_day"]):
        out.append("funding 7 วันไม่ครบ")
    return out


EVENTS = {
    "trend_up": ("ราคาปิดกลับขึ้นเหนือค่าเฉลี่ย 100 วัน", "ราคาปิดหลุดค่าเฉลี่ย 100 วัน"),
    "funding_hot": ("funding เฉลี่ย 7 วันสูงเกิน 0.03%/8 ชม. (ตลาดใช้เลเวอเรจหนัก)", "funding กลับสู่ระดับปกติ"),
    "high_vol": ("ความผันผวน 30 วันสูงเกิน 80%/ปี", "ความผันผวนกลับสู่ระดับปกติ"),
}


def events(sig: pd.DataFrame) -> list[str]:
    """State changes between the last two closed days, plus standing warnings."""
    if len(sig) < 2:
        return []
    today, prev = sig.iloc[-1], sig.iloc[-2]
    out = []
    for col, (on, off) in EVENTS.items():
        if pd.notna(today[col]) and pd.notna(prev[col]) and bool(today[col]) != bool(prev[col]):
            out.append(on if bool(today[col]) else off)
    if pd.notna(today["drawdown_365d"]) and pd.notna(prev["drawdown_365d"]) \
            and today["drawdown_365d"] >= DRAWDOWN_WARN and prev["drawdown_365d"] < DRAWDOWN_WARN:
        out.append(f"ราคาลงจากจุดสูงสุด 1 ปีเกิน {DRAWDOWN_WARN:.0%}")
    return out


def _num(x):
    return None if pd.isna(x) else float(x)


def _flag(x):
    return None if x is None or pd.isna(x) else bool(x)


def summary(sig: pd.DataFrame, funding_estimated: bool) -> dict:
    t = sig.iloc[-1]
    w = _num(t["suggested_btc_weight"])
    prev_w = _num(sig["suggested_btc_weight"].iloc[-2]) if len(sig) > 1 else w
    f = _num(t["funding_7d_per_day"])
    out = {
        "date": str(sig.index[-1].date()),
        "close": _num(t["close"]),
        "sma100": _num(t["sma100"]),
        "dist_to_sma": _num(t["dist_to_sma"]),
        "trend_up": _flag(t["trend_up"]),
        "funding_7d_per_8h": None if f is None else f / 3,
        "funding_hot": _flag(t["funding_hot"]),
        "funding_estimated": funding_estimated,
        "vol_30d": _num(t["vol_30d"]),
        "drawdown_365d": _num(t["drawdown_365d"]),
        "suggested_btc_weight": w,                     # None = not enough data, no suggestion
        "missing_inputs": missing_inputs(sig),
        "weight_changed": w is not None and prev_w is not None and w != prev_w,
        "events": events(sig),
    }
    if "oi_7d_change" in sig and pd.notna(t.get("oi_7d_change")):
        out["oi_usdt"] = float(t["oi_usdt"])
        out["oi_7d_change"] = float(t["oi_7d_change"])
    return out


def render_th(s: dict) -> str:
    na = "ข้อมูลไม่พอ"
    pct = lambda x, f: na if x is None else format(x, f)
    lines = [f"BTC ปิดวันที่ {s['date']} (UTC): " + (na if s["close"] is None else f"{s['close']:,.0f} USDT")]
    if s["trend_up"] is None:
        lines.append(f"- เทียบค่าเฉลี่ย 100 วัน: {na}")
    else:
        trend = "เหนือ" if s["trend_up"] else "ใต้"
        lines.append(f"- ราคาอยู่{trend}ค่าเฉลี่ย 100 วัน ({s['sma100']:,.0f}) ห่าง {s['dist_to_sma']:+.1%}")
    if s["funding_hot"] is None:
        lines.append(f"- funding เฉลี่ย 7 วัน: {na}")
    else:
        lines.append(f"- funding เฉลี่ย 7 วัน {s['funding_7d_per_8h']:.4%}/8 ชม."
                     + (" (สูงผิดปกติ)" if s["funding_hot"] else " (ปกติ)")
                     + (" บางวันเป็นค่าประมาณ" if s["funding_estimated"] else ""))
    lines.append(f"- ความผันผวน 30 วัน {pct(s['vol_30d'], '.0%')}/ปี, ลงจากจุดสูงสุด 1 ปี {pct(s['drawdown_365d'], '.1%')}")
    if "oi_7d_change" in s:
        lines.append(f"- open interest {s['oi_usdt'] / 1e9:,.1f} พันล้าน USDT, เปลี่ยน {s['oi_7d_change']:+.1%} ใน 7 วัน")
    if s["suggested_btc_weight"] is None:
        lines.append(f"- สัดส่วน BTC ตามกติกาที่ทดสอบ: {na} ไม่สร้างคำแนะนำวันนี้ ({', '.join(s['missing_inputs'])})")
    else:
        lines.append(f"- สัดส่วน BTC ตามกติกาที่ทดสอบ: {s['suggested_btc_weight']:.0%}"
                     + (" (เปลี่ยนจากเมื่อวาน)" if s["weight_changed"] else ""))
    return "\n".join(lines)
