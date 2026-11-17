"""R14: ADX/ATR filters on R10a, pre-declared in docs/research/r14-adx-atr-filter-plan.md.

    PYTHONPATH=research/spike uv run python research/spike/run_r14.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable
from run_r10 import BAND, latch

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars, regularize_h1

EQUITY = 10_000.0
WINDOWS = {
    "dev": (Path("data/binance_um"), "2022-05-01", "2026-01-01"),
    "C1": (Path("data/binance_um_c1"), "2020-04-15", "2022-01-01"),
}
N = 14


def naive(x):
    if x.index.tz is not None:
        x.index = x.index.tz_localize(None)
    return x


def hl(h1_raw: pd.DataFrame, idx) -> pd.DataFrame:
    h1 = regularize_h1(h1_raw)
    g = h1.groupby(h1.index.floor("1D"))
    d = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "ok": g["valid"].sum() == 24})
    d.loc[~d["ok"], ["high", "low"]] = np.nan
    return naive(d).reindex(idx)


def wilder(x: pd.Series) -> pd.Series:
    return x.ewm(alpha=1 / N, adjust=False, min_periods=N, ignore_na=True).mean()


def atr_adx(h: pd.Series, l: pd.Series, c: pd.Series) -> tuple[pd.Series, pd.Series]:
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1, skipna=False)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index).where(up.notna() & dn.notna())
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index).where(up.notna() & dn.notna())
    atr = wilder(tr)
    pdi, mdi = 100 * wilder(pdm) / atr, 100 * wilder(mdm) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return atr, wilder(dx)


def metrics(eq: pd.Series) -> dict:
    return {"net": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}


def run(name: str, d: Path, start: str, end: str) -> dict:
    raw = load(d, "BTCUSDT", "1h")
    daily = naive(daily_bars(raw))
    f = naive(load_funding(d, "BTCUSDT"))
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    c, sma = sig["close"], sig["sma100"]
    x = hl(raw, daily.index)
    atr, adx = atr_adx(x["high"], x["low"], daily["close"])
    atr, adx = atr.reindex(sig.index), adx.reindex(sig.index)
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    out_rule = lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND)  # noqa: E731
    up = lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND)  # noqa: E731
    st = {
        "R10a": latch(c, sma, up, out_rule),
        "R14a_adx20": latch(c, sma, lambda i: up(i) and bool(adx.iloc[i] > 20), out_rule),
        "R14b_adx25": latch(c, sma, lambda i: up(i) and bool(adx.iloc[i] > 25), out_rule),
        "R14c_atr_band": latch(c, sma, lambda i: bool(c.iloc[i] > sma.iloc[i] + atr.iloc[i]),
                               lambda i: bool(c.iloc[i] < sma.iloc[i] - atr.iloc[i])),
    }
    w = {k: v * fund for k, v in st.items()} | {"buy_and_hold": pd.Series(1.0, index=sig.index)}
    days = sig.index
    win = (days >= pd.Timestamp(start)) & (days < pd.Timestamp(end))
    years = sorted({t.year for t in days[win]})
    rep: dict = {"window": [start, end], "days": int(win.sum()),
                 "adx_nan_in_window": int(adx[win].isna().sum()), "atr_nan_in_window": int(atr[win].isna().sum()),
                 "adx_median": float(adx[win].median()), "atr_pct_median": float((atr / c)[win].median()),
                 "days_in": {k: int((v[win] > 0).sum()) for k, v in st.items()},
                 "state_changes": {k: int((v[win].diff().abs() > 0).sum()) for k, v in st.items()},
                 "runs": {}}
    out = Path("reports/r14") / name
    out.mkdir(parents=True, exist_ok=True)
    for cn, costs in (("base_costs", RegimeCosts()), ("costs_x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        rep["runs"][cn] = {}
        for k, ws in w.items():
            eq, tr = simulate_weights(daily, ws, start, end, equity=EQUITY, costs=costs)
            r = metrics(eq) | {"trades": int(len(tr))}
            r["years"] = {str(y): metrics(eq[(eq.index >= pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=1))
                                             & (eq.index < pd.Timestamp(f"{y + 1}-01-01"))]) for y in years}
            rep["runs"][cn][k] = r
            if cn == "base_costs":
                eq.to_csv(out / f"equity_{k}.csv")
    return rep


def main() -> None:
    np.seterr(all="ignore")
    report = {"plan": "docs/research/r14-adx-atr-filter-plan.md", "windows": {}}
    for name, (d, a, b) in WINDOWS.items():
        report["windows"][name] = run(name, d, a, b)
    verdict = {}
    for v in ("R14a_adx20", "R14b_adx25", "R14c_atr_band"):
        per = {}
        for name in WINDOWS:
            r = report["windows"][name]["runs"]["base_costs"]
            n, b = r[v], r["R10a"]
            per[name] = bool(n["sharpe"] > b["sharpe"] and n["max_dd"] <= b["max_dd"] and n["net"] > 0)
        verdict[v] = per | {"pass": all(per.values())}
    report["verdict"] = verdict
    Path("reports/r14/report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
