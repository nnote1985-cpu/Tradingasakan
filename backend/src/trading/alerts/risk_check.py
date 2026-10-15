"""Daily BTC risk check for path A (BTC core, alerts only, a person decides).

Signals come from the rules tested in docs/research (v2 trend state, v3
funding overlay). Neither beat BTC buy & hold in the 2024 bull year; they are
alerts with a known cost, not a market-beating system.
"""

from __future__ import annotations

import math

import pandas as pd

SMA_DAYS = 100
FUNDING_WINDOW = 7
FUNDING_THRESHOLD_PER_DAY = 0.0009     # 0.03% per 8h
VOL_DAYS = 30
HIGH_VOL = 0.80                        # annualised; BTC's long-run level is roughly 0.5-0.7
DRAWDOWN_WARN = 0.20


def signals(daily: pd.DataFrame, funding_daily: pd.Series, oi: pd.Series | None = None) -> pd.DataFrame:
    """One row per day with every signal known at that day's close."""
    c = daily["close"].astype(float)
    out = pd.DataFrame(index=daily.index)
    out["close"] = c
    out["sma100"] = c.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    out["trend_up"] = c > out["sma100"]
    out["dist_to_sma"] = c / out["sma100"] - 1
    f = funding_daily.reindex(daily.index)
    out["funding_7d_per_day"] = f.rolling(FUNDING_WINDOW, min_periods=FUNDING_WINDOW).mean()
    out["funding_hot"] = out["funding_7d_per_day"] > FUNDING_THRESHOLD_PER_DAY
    r = (c / c.shift(1)).apply(math.log)
    out["vol_30d"] = r.rolling(VOL_DAYS, min_periods=VOL_DAYS).std() * math.sqrt(365)
    out["high_vol"] = out["vol_30d"] > HIGH_VOL
    out["drawdown_365d"] = 1 - c / c.rolling(365, min_periods=1).max()
    if oi is not None and len(oi):
        o = oi.reindex(daily.index)
        out["oi_usdt"] = o
        out["oi_7d_change"] = o / o.shift(7) - 1
    out["suggested_btc_weight"] = out["trend_up"].astype(float) * out["funding_hot"].map({True: 0.5, False: 1.0})
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
    if today["drawdown_365d"] >= DRAWDOWN_WARN and prev["drawdown_365d"] < DRAWDOWN_WARN:
        out.append(f"ราคาลงจากจุดสูงสุด 1 ปีเกิน {DRAWDOWN_WARN:.0%}")
    return out


def summary(sig: pd.DataFrame, funding_estimated: bool) -> dict:
    t = sig.iloc[-1]
    prev_w = sig["suggested_btc_weight"].iloc[-2] if len(sig) > 1 else t["suggested_btc_weight"]
    out = {
        "date": str(sig.index[-1].date()),
        "close": float(t["close"]),
        "sma100": float(t["sma100"]),
        "dist_to_sma": float(t["dist_to_sma"]),
        "trend_up": bool(t["trend_up"]),
        "funding_7d_per_8h": float(t["funding_7d_per_day"]) / 3,
        "funding_hot": bool(t["funding_hot"]),
        "funding_estimated": funding_estimated,
        "vol_30d": float(t["vol_30d"]),
        "drawdown_365d": float(t["drawdown_365d"]),
        "suggested_btc_weight": float(t["suggested_btc_weight"]),
        "weight_changed": bool(t["suggested_btc_weight"] != prev_w),
        "events": events(sig),
    }
    if "oi_7d_change" in sig and pd.notna(t.get("oi_7d_change")):
        out["oi_usdt"] = float(t["oi_usdt"])
        out["oi_7d_change"] = float(t["oi_7d_change"])
    return out


def render_th(s: dict) -> str:
    trend = "เหนือ" if s["trend_up"] else "ใต้"
    lines = [
        f"BTC ปิดวันที่ {s['date']} (UTC): {s['close']:,.0f} USDT",
        f"- ราคาอยู่{trend}ค่าเฉลี่ย 100 วัน ({s['sma100']:,.0f}) ห่าง {s['dist_to_sma']:+.1%}",
        f"- funding เฉลี่ย 7 วัน {s['funding_7d_per_8h']:.4%}/8 ชม."
        + (" (สูงผิดปกติ)" if s["funding_hot"] else " (ปกติ)")
        + (" บางวันเป็นค่าประมาณ" if s["funding_estimated"] else ""),
        f"- ความผันผวน 30 วัน {s['vol_30d']:.0%}/ปี, ลงจากจุดสูงสุด 1 ปี {s['drawdown_365d']:.1%}",
    ]
    if "oi_7d_change" in s:
        lines.append(f"- open interest {s['oi_usdt'] / 1e9:,.1f} พันล้าน USDT, เปลี่ยน {s['oi_7d_change']:+.1%} ใน 7 วัน")
    lines.append(f"- สัดส่วน BTC ตามกติกาที่ทดสอบ: {s['suggested_btc_weight']:.0%}"
                 + (" (เปลี่ยนจากเมื่อวาน)" if s["weight_changed"] else ""))
    return "\n".join(lines)
