"""Order book prototype: does OBI + order flow filter the EMA/ADX/ATR pullback signals? (tooling check)

    uv run python research/orderflow/run_obi_filter.py [--tf 15min] [--htf 1h] [--out DIR]

Data: data/orderflow/ from research/orderflow/download.py (data.binance.vision, BTCUSDT USD-M,
2026-11-01..16, after the C2 window). Too few days for evidence: results are a sanity check of
the feature code and the A/B harness, not a basis for any rule.

Bot A: EMA20/50/200 trend + HTF EMA50/200 + EMA50 slope >= 0.3 ATR + ADX >= 20 + < 3 EMA50 crosses
       in 20 bars; entry on EMA20 pullback-reclaim or EMA20/50 cross (rules of the
       EMA_Trend_Pullback review, ema-pullback-review/etp_backtest.py), long and short.
Bot B: the same signals, taken only when order book imbalance (0.2% band, bar mean) and taker
       trade delta of the signal bar both agree with the trade direction (sign only, no tuning).

Risk (futures standard): exchange-side stop on every trade at 1.5 ATR, size so the stop loses 1%
of equity (notional capped at 3x equity), TP 2R, exit next open when close crosses EMA50,
3% daily loss cap, 10% total drawdown cap. Exits are resolved on 1m bars; when one 1m bar touches
both stop and target, the stop is assumed. Cost 0.05% per side (taker 0.04% + slippage).
Funding ignored (holds are hours).
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from trading.features.orderbook import (
    near_depth_ratio_from_bookdepth,
    obi_from_bookdepth,
    trade_flow_bars,
)

DATA = Path("data/orderflow")
EVAL_START = pd.Timestamp("2026-11-05", tz="UTC")  # Nov 1-4 = indicator warm-up only
COST = 0.0005
RISK = 0.01
MAX_LEV = 3.0
DAY_CAP = 0.03
TOTAL_CAP = 0.10


def _csv_from_zip(path: Path, **kw) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            return pd.read_csv(f, **kw)


def _ms(x: pd.Series) -> pd.DatetimeIndex:
    v = x.astype("int64")
    return pd.to_datetime(np.where(v > 1e14, v // 1000, v), unit="ms", utc=True)


def load_1m() -> pd.DataFrame:
    fs = sorted((DATA / "klines_1m").glob("*.zip"))
    df = pd.concat([_csv_from_zip(f, usecols=range(6)) for f in fs])
    df.columns = ["t", "o", "h", "l", "c", "v"]
    df.index = _ms(df.pop("t"))
    return df.sort_index()


def load_trades() -> pd.DataFrame:
    parts = []
    for f in sorted((DATA / "aggTrades").glob("*.zip")):
        d = _csv_from_zip(f, usecols=["quantity", "transact_time", "is_buyer_maker"])
        d.index = _ms(d.pop("transact_time"))
        parts.append(d.rename(columns={"quantity": "qty"}))
    return pd.concat(parts).sort_index()


def load_depth() -> pd.DataFrame:
    df = pd.concat([_csv_from_zip(f) for f in sorted((DATA / "bookDepth").glob("*.zip"))])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["percentage"] = df["percentage"].astype(float)
    return df


def resample(d: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}
    return d.resample(rule, label="left", closed="left").agg(agg).dropna()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def signals(d: pd.DataFrame, htf: str) -> pd.DataFrame:
    """EMA_Trend_Pullback signals (same formulas as ema-pullback-review/etp_backtest.py)."""
    d = d.copy()
    c, h, lo = d.c, d.h, d.l
    d["ef"], d["em"], d["es"] = ema(c, 20), ema(c, 50), ema(c, 200)
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    up, dn = h.diff(), -lo.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), d.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), d.index)
    w = lambda s: s.ewm(alpha=1 / 14, adjust=False).mean()  # noqa: E731
    atrw = w(tr)
    pdi, mdi = 100 * w(pdm) / atrw, 100 * w(mdm) / atrw
    d["adx"] = w(100 * (pdi - mdi).abs() / (pdi + mdi))
    H = resample(d[["o", "h", "l", "c", "v"]], htf)
    # last CLOSED htf bar: shift by one htf bar, then label it at that bar's close time
    hsig = np.sign(ema(H.c, 50) - ema(H.c, 200))
    hsig.index = hsig.index + pd.Timedelta(htf)
    tf = d.index[1] - d.index[0]
    d["htf"] = hsig.reindex(d.index + tf, method="ffill").to_numpy()
    trend = np.where((c > d.es) & (d.em > d.es), 1, np.where((c < d.es) & (d.em < d.es), -1, 0))
    slope = (d.em - d.em.shift(5)) / d.atr
    side = np.sign(c - d.em)
    crosses = ((side * side.shift()) < 0).astype(int).rolling(20).sum()
    dist = (c - d.ef) / d.atr
    efm = d.ef - d.em
    xup = (efm.shift(2) <= 0) & (efm.shift(1) > 0) & (efm > 0)
    xdn = (efm.shift(2) >= 0) & (efm.shift(1) < 0) & (efm < 0)
    xgap = efm.abs() >= 0.15 * d.atr
    cross = np.where(xup & xgap, 1, np.where(xdn & xgap, -1, 0))
    touch_up = (lo <= d.ef).astype(int).shift(1).rolling(5).max() > 0
    touch_dn = (h >= d.ef).astype(int).shift(1).rolling(5).max() > 0
    rec_up = (c.shift() <= d.ef.shift()) & (c > d.ef)
    rec_dn = (c.shift() >= d.ef.shift()) & (c < d.ef)
    sig = np.zeros(len(d), int)
    for dd in (1, -1):
        base = (trend == dd) & (d.htf == dd) & (dd * slope >= 0.3) & (d.adx >= 20) & (crosses < 3)
        pb = (touch_up & rec_up & (c > d.em)) if dd == 1 else (touch_dn & rec_dn & (c < d.em))
        pb = pb & (dd * dist <= 1.5)
        sig[(base & (pb | (cross == dd))).to_numpy()] = dd
    d["sig"] = sig
    return d


def simulate(d: pd.DataFrame, m1: pd.DataFrame, take: np.ndarray, tf: pd.Timedelta) -> tuple[pd.DataFrame, pd.Series]:
    """Trade the signals where ``take`` is True. Returns trades and the per-bar equity curve."""
    eq, peak, halted = 1.0, 1.0, False
    day_start: dict[pd.Timestamp, float] = {}
    trades = []
    curve = pd.Series(np.nan, index=d.index)
    i = int(d.index.searchsorted(EVAL_START))
    idx = d.index
    while i < len(d) - 1:
        t_close = idx[i] + tf
        day = t_close.normalize()
        day_start.setdefault(day, eq)
        curve.iloc[i] = eq
        if halted or not take[i] or d.sig.iat[i] == 0 or eq <= day_start[day] * (1 - DAY_CAP):
            i += 1
            continue
        pos = int(d.sig.iat[i])
        atr = float(d.atr.iat[i])
        entry = float(d.o.iat[i + 1])
        sl = entry - pos * 1.5 * atr
        tp = entry + pos * 3.0 * atr
        stop_frac = abs(entry - sl) / entry
        lev = min(RISK / stop_frac, MAX_LEV)
        # walk 1m bars from the entry bar; check signal-TF close vs EMA50 at each TF bar close
        j = i + 1
        exit_px, why, exit_t = None, None, None
        while j < len(d) and exit_px is None:
            seg = m1.loc[idx[j] : idx[j] + tf - pd.Timedelta("1min")]
            for t, r in seg.iterrows():
                hit_sl = r.l <= sl if pos == 1 else r.h >= sl
                hit_tp = r.h >= tp if pos == 1 else r.l <= tp
                if hit_sl:
                    gap = (r.o < sl) if pos == 1 else (r.o > sl)
                    exit_px, why, exit_t = (r.o if gap else sl), "SL", t
                    break
                if hit_tp:
                    exit_px, why, exit_t = tp, "TP", t
                    break
            if exit_px is None:
                closed_wrong = d.c.iat[j] < d.em.iat[j] if pos == 1 else d.c.iat[j] > d.em.iat[j]
                if closed_wrong and j + 1 < len(d):
                    exit_px, why, exit_t = float(d.o.iat[j + 1]), "EMA50", idx[j + 1]
                    j += 1
                elif j + 1 >= len(d):
                    exit_px, why, exit_t = float(d.c.iat[j]), "END", idx[j] + tf
            if exit_px is None:
                curve.iloc[j] = eq
                j += 1
        ret = pos * (exit_px / entry - 1)
        pnl = lev * (ret - 2 * COST)
        eq *= 1 + pnl
        peak = max(peak, eq)
        if eq <= peak * (1 - TOTAL_CAP):
            halted = True
        trades.append(
            {
                "signal_t": idx[i] + tf,
                "side": pos,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "lev": round(lev, 3),
                "exit_t": exit_t,
                "exit": exit_px,
                "why": why,
                "r_mult": ret / stop_frac,
                "pnl_pct": pnl * 100,
            }
        )
        i = max(j, i + 1)
    curve = curve.ffill().fillna(1.0)
    curve.iloc[-1] = eq
    return pd.DataFrame(trades), curve


def _spearman(x: pd.Series, y: pd.Series) -> float:
    """Rank correlation without scipy."""
    return round(float(x.rank().corr(y.rank())), 4)


def summary(T: pd.DataFrame, curve: pd.Series) -> dict:
    if T.empty:
        return {"trades": 0}
    dd = float((1 - curve / curve.cummax()).max())
    return {
        "trades": int(len(T)),
        "long": int((T.side == 1).sum()),
        "short": int((T.side == -1).sum()),
        "win_rate": round(float((T.pnl_pct > 0).mean()), 3),
        "sum_R": round(float(T.r_mult.sum()), 2),
        "return_pct": round(float((curve.iloc[-1] - 1) * 100), 2),
        "max_dd_pct": round(dd * 100, 2),
        "exits": T.why.value_counts().to_dict(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min")
    ap.add_argument("--htf", default="1h")
    ap.add_argument("--out", default="research/results/orderflow-prototype")
    a = ap.parse_args()
    tf = pd.Timedelta(a.tf)
    out = Path(a.out) / a.tf
    out.mkdir(parents=True, exist_ok=True)

    m1 = load_1m()
    bars = signals(resample(m1, a.tf), a.htf)

    depth = load_depth()
    obi = obi_from_bookdepth(depth, 0.2)
    near = near_depth_ratio_from_bookdepth(depth)
    flow = trade_flow_bars(load_trades(), a.tf)
    feat = pd.DataFrame({"obi02": obi}).join(near).resample(a.tf, label="left", closed="left").mean()
    bars = bars.join(feat).join(flow[["delta", "delta_ratio", "cvd"]])
    bars["fwd_ret"] = bars.c.shift(-1) / bars.c - 1  # next-bar return, diagnostics only

    sig = bars.sig.to_numpy()
    agree = (np.sign(bars.obi02) == sig) & (np.sign(bars.delta_ratio) == sig)
    obi_ok = (np.sign(bars.obi02) == sig).to_numpy()
    flow_ok = (np.sign(bars.delta_ratio) == sig).to_numpy()
    # A and B are the two bots asked for; B_obi_only / B_flow_only show which half of B does the filtering
    runs = {"A": np.ones(len(bars), bool), "B": agree.to_numpy(), "B_obi_only": obi_ok, "B_flow_only": flow_ok}
    report: dict = {"tf": a.tf, "htf": a.htf, "eval_start": str(EVAL_START), "eval_end": str(bars.index[-1] + tf)}
    for name, take in runs.items():
        T, curve = simulate(bars, m1, take, tf)
        report[f"bot_{name}"] = summary(T, curve)
        T.to_csv(out / f"trades_bot_{name}.csv", index=False)
        curve.to_csv(out / f"equity_bot_{name}.csv", header=["equity"])

    ev = bars[bars.index >= EVAL_START].dropna(subset=["obi02", "delta_ratio", "fwd_ret"])
    report["feature_check"] = {
        "bars": int(len(ev)),
        "obi_bars_missing": int(bars.loc[bars.index >= EVAL_START, "obi02"].isna().sum()),
        "spearman_next_bar_ret": {k: _spearman(ev[k], ev.fwd_ret) for k in ("obi02", "delta_ratio", "bid_near_ratio", "ask_near_ratio")},
        "obi02_vs_delta_ratio": _spearman(ev.obi02, ev.delta_ratio),
    }
    s = bars[(bars.index >= EVAL_START) & (bars.sig != 0)]
    report["signals"] = {
        "total": int(len(s)),
        "pass_filter": int(agree[s.index].sum()),
        "rows": [
            {
                "t": str(t + tf),
                "side": int(r.sig),
                "obi02": round(float(r.obi02), 3),
                "delta_ratio": round(float(r.delta_ratio), 3),
                "pass": bool(agree[t]),
                "next_bar_ret_pct": round(float(r.fwd_ret) * 100, 3),
            }
            for t, r in s.iterrows()
        ],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "signals"}, indent=2, default=str))
    print("signals:", report["signals"]["total"], "pass B filter:", report["signals"]["pass_filter"])


if __name__ == "__main__":
    main()
