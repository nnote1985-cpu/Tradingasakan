"""R13 E1 Donchian_Breakout as a forward spot book rule (docs/e1-paper-book-plan.md).

The rule is the R13 port of DonchianBreakoutEA.mq5 (logiccrafterdz/argus, MIT, Copyright 2026
LogicCrafterDz; docs/third_party/argus-LICENSE.txt), copied here unchanged from
research/spike/ea_ports.py (E1Donchian and the spot/native path of ``simulate``). The only addition is
that signals and fills can come from different series (perp signals, spot fills) and the spot.v1
HALT. ``replay`` is a pure function of its inputs: the book re-runs it every day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

RULES_VERSION = "spot-e1-v1"
POINT = 0.01                  # 2-digit BTCUSD symbol (R13 port rules)
RISK = 0.01                   # 1% of equity from entry to stop
TP_R = 2.5
HALT_DD, ALERT_DD, ALERT_DAY = 0.25, 0.15, 0.05


@dataclass(frozen=True)
class Costs:
    fee: float = 0.0010
    slip: float = 0.0002
    stop_slip: float = 0.0005


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=2.0 / (n + 1), adjust=False).mean().to_numpy(copy=True)


def adx(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    """MT5 iADX main line (EMA smoothing), as in the R13 port."""
    ph, pl, pc = np.r_[np.nan, h[:-1]], np.r_[np.nan, lo[:-1]], np.r_[np.nan, c[:-1]]
    pdm, ndm = h - ph, pl - lo
    pdm, ndm = np.where(pdm < 0, 0.0, pdm), np.where(ndm < 0, 0.0, ndm)
    keep_p, keep_n = pdm > ndm, ndm > pdm
    pdm, ndm = np.where(keep_p, pdm, 0.0), np.where(keep_n, ndm, 0.0)
    tr = np.fmax(h, pc) - np.fmin(lo, pc)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_raw = np.where(tr > 0, 100.0 * pdm / tr, 0.0)
        n_raw = np.where(tr > 0, 100.0 * ndm / tr, 0.0)
    p_raw[0] = n_raw[0] = 0.0
    pdi, mdi = ema(p_raw, n), ema(n_raw, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(pdi + mdi > 0, 100.0 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    out = ema(dx, n)
    out[: 2 * n] = np.nan
    return out


def h4_bars(h1: pd.DataFrame) -> pd.DataFrame:
    g = h1.resample("4h", origin="epoch", label="left", closed="left")
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})


class E1Signals:
    """Indicators on H4 signal bars; ``b`` is the last closed bar."""

    min_bars = 210

    def __init__(self, h4: pd.DataFrame):
        self.t = h4.index
        self.o, self.h, self.l, self.c = (h4[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.e50, self.e200 = ema(self.c, 50), ema(self.c, 200)
        self.adx = adx(self.h, self.l, self.c, 14)
        self.hi20 = pd.Series(self.h).rolling(20, min_periods=20).max().to_numpy()
        self.lo20 = pd.Series(self.l).rolling(20, min_periods=20).min().to_numpy()

    def trail(self, b: int, sl: float) -> float | None:
        new = self.lo20[b]
        return float(new) if new > sl + POINT else None

    def entry_stop(self, b: int) -> float | None:
        """Stop price for a long entry at bar b+1's open, or None (no long signal)."""
        if b < self.min_bars or not (self.adx[b] >= 20 and self.adx[b] > self.adx[b - 1]):
            return None
        c = self.c[b]
        if c > self.hi20[b - 1] and c > self.e50[b] and c > self.e200[b]:
            return float(self.lo20[b] - 2 * POINT)
        return None


def replay(sig_h1: pd.DataFrame, ex_h1: pd.DataFrame, ex_m1: pd.DataFrame, start: str | pd.Timestamp,
           end: str | pd.Timestamp, *, equity0: float = 10_000.0, costs: Costs = Costs(), halt: bool = True) -> dict[str, Any]:
    """Run the book from ``start`` (an H4 bar open) to ``end`` (exclusive). ``sig_h1`` may start earlier
    (warm-up). ``ex_h1``/``ex_m1`` give fills and marks. Returns closed trades, the open position, the
    event log, hourly and daily equity. A position open at ``end`` stays open (no closing fill)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    sig = E1Signals(h4_bars(sig_h1))
    ex_h1 = ex_h1.loc[(ex_h1.index >= start) & (ex_h1.index < end)]
    ho, hc = ex_h1["open"].to_numpy(float), ex_h1["close"].to_numpy(float)
    h_t = ex_h1.index
    m1 = ex_m1.loc[(ex_m1.index >= start) & (ex_m1.index < end)]
    m_pos = {t: i for i, t in enumerate(m1.index)}
    mo, mh, ml = (m1[k].to_numpy(float) for k in ("open", "high", "low"))

    cash, qty = equity0, 0.0
    pos: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    marks: dict[pd.Timestamp, float] = {}
    peak, halted = equity0, False

    def close(px_raw: float, t: pd.Timestamp, reason: str, slip: float) -> None:
        nonlocal cash, qty, pos
        assert pos is not None
        px = px_raw * (1 - slip)
        cash += qty * px * (1 - costs.fee)
        pos.update(exit_time=t, exit_px=px, reason=reason, equity_after=cash)
        trades.append(pos)
        events.append({"kind": "close", "time": t, "price": px, "reason": reason, "equity": cash})
        qty, pos = 0.0, None

    j0, j1 = int(sig.t.searchsorted(start)), int(sig.t.searchsorted(end))
    for j in range(j0, j1):
        b, t_open = j - 1, sig.t[j]
        hi0 = int(np.searchsorted(h_t, t_open))
        if hi0 >= len(h_t) or h_t[hi0] != t_open or not np.isfinite(ho[hi0]):
            continue
        px_open = ho[hi0]
        if pos is not None:
            new = sig.trail(b, pos["sl"])
            if new is not None:
                pos["sl"], pos["tp"] = new, None
                events.append({"kind": "stop_moved", "time": t_open, "stop": new})
        if pos is None and not halted:
            stop = sig.entry_stop(b)
            if stop is not None and stop < px_open:
                eq, dist = cash, px_open - stop
                notional = min(eq * RISK / dist * px_open, eq)
                px = px_open * (1 + costs.slip)
                qty = notional * (1 - costs.fee) / px
                cash -= notional
                pos = {"entry_time": t_open, "side": 1, "entry_px": px, "sl0": stop, "tp0": px_open + TP_R * dist,
                       "notional": notional, "equity_before": eq, "sl": stop, "tp": px_open + TP_R * dist}
                events.append({"kind": "open", "time": t_open, "price": px, "qty": qty, "stop": stop, "target": pos["tp"]})
        for hi in range(hi0, min(hi0 + 4, len(h_t))):
            th = h_t[hi]
            if pos is not None:
                k = m_pos.get(th)
                if k is not None and len(mo[k:k + 60]) == 60 and np.isfinite(mo[k:k + 60]).all():
                    lo_, hi_, op_ = ml[k:k + 60], mh[k:k + 60], mo[k:k + 60]
                else:
                    lo_, hi_, op_ = np.array([ex_h1["low"].iloc[hi]]), np.array([ex_h1["high"].iloc[hi]]), np.array([ho[hi]])
                hit_sl = np.flatnonzero(lo_ <= pos["sl"])
                hit_tp = np.flatnonzero(hi_ >= pos["tp"]) if pos["tp"] is not None else np.array([], int)
                k_sl = hit_sl[0] if len(hit_sl) else 10**9
                k_tp = hit_tp[0] if len(hit_tp) else 10**9
                if k_sl <= k_tp and k_sl < 10**9:
                    close(min(pos["sl"], op_[k_sl]), th + pd.Timedelta(minutes=int(k_sl)), "stop", costs.stop_slip)
                elif k_tp < 10**9:
                    close(max(pos["tp"], op_[k_tp]), th + pd.Timedelta(minutes=int(k_tp)), "target", costs.slip)
            eq = cash + qty * hc[hi]
            marks[th] = eq
            peak = max(peak, eq)
            if halt and not halted and eq <= peak * (1 - HALT_DD):
                halted = True
                events.append({"kind": "halt", "time": th, "equity": eq, "peak": peak})
                if pos is not None and hi + 1 < len(h_t):
                    close(ho[hi + 1], h_t[hi + 1], "halt", costs.slip)
    eq_h = pd.Series(marks, dtype=float).sort_index()
    hours = pd.DatetimeIndex(eq_h.index)
    daily = eq_h[hours.hour == 23]
    daily.index = pd.DatetimeIndex(daily.index).floor("1D")
    if len(daily):
        daily = pd.concat([pd.Series([equity0], index=[daily.index[0] - pd.Timedelta(days=1)]), daily])
    events += alerts(daily)
    events.sort(key=lambda e: pd.Timestamp(e["time"]))
    open_pos = None if pos is None else {**pos, "qty": qty, "mark": float(hc[-1]) if len(hc) else None}
    return {"trades": trades, "open_position": open_pos, "events": events, "equity_hourly": eq_h,
            "equity_daily": daily, "cash": cash, "qty": qty, "halted": halted}


def alerts(daily: pd.Series) -> list[dict[str, Any]]:
    """spot.v1 alert events on daily closes: day loss ≥ 5%, drawdown first reaching 15%."""
    out: list[dict[str, Any]] = []
    peak: float | None = None
    prev: float | None = None
    dd_flag = False
    for t_raw, v in daily.items():
        t = pd.Timestamp(str(t_raw))
        peak = v if peak is None else max(peak, v)
        if prev is not None and v <= prev * (1 - ALERT_DAY):
            out.append({"kind": "alert", "time": t + pd.Timedelta(hours=23), "alert": f"ขาดทุนวันเดียว {v / prev - 1:.1%}"})
        dd = 1 - v / peak
        if dd >= ALERT_DD and not dd_flag:
            out.append({"kind": "alert", "time": t + pd.Timedelta(hours=23), "alert": f"drawdown {dd:.1%}"})
            dd_flag = True
        elif dd < ALERT_DD:
            dd_flag = False
        prev = v
    return out
