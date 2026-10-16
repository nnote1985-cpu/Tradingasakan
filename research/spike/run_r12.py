"""R12: R10a band plus R9b volatility sizing, pre-declared in
docs/research/r12-band-voltarget-plan.md (commit 3f8a1c0). Run once; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r12.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable, load_config
from run_r9 import MED_MIN, MED_WINDOW
from run_r10 import BAND, latch

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

EQUITY = 10_000.0
START, END = pd.Timestamp("2022-07-01"), pd.Timestamp("2026-01-01")
YEARS = [("2022 (Jul-Dec)", "2022-07-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
         ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]


def metrics(eq: pd.Series) -> dict:
    return {"net_return": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}


def episodes(eq: pd.Series, n: int = 3) -> list[dict]:
    """Worst n non-overlapping drawdowns: peak, trough, recovery (None if not recovered)."""
    dd = (1 - eq / eq.cummax()).copy()
    out = []
    for _ in range(n):
        t = dd.idxmax()
        if dd.loc[t] <= 0:
            break
        p = eq[:t].idxmax()
        rec = eq[t:][eq[t:] >= eq.loc[p]]
        r = rec.index[0] if len(rec) else None
        out.append({"peak": str(p.date()), "trough": str(t.date()), "recovered": str(r.date()) if r is not None else None,
                    "depth": float(dd.loc[t])})
        dd[(dd.index >= p) & ((dd.index <= r) if r is not None else True)] = 0
    return out


def main() -> None:
    d = Path(load_config("configs/spike.json")["data_dir"])
    daily = daily_bars(load(d, "BTCUSDT", "1h"))
    daily.index = daily.index.tz_localize(None) if daily.index.tz is not None else daily.index
    f = load_funding(d, "BTCUSDT")
    f.index = f.index.tz_localize(None) if f.index.tz is not None else f.index
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    c, sma = sig["close"], sig["sma100"]
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND))
    vol = sig["vol_30d"]                                   # R9b scale, as in run_r9.py
    med = vol.rolling(MED_WINDOW, min_periods=MED_MIN).median()
    raw = (med / vol).clip(upper=1.0)
    scale = (np.floor(raw * 4) / 4).clip(lower=0.25).where(raw.notna(), 1.0)
    w = {"pathA": sig["suggested_btc_weight"], "R10a_band": band * fund, "R12_band_vol": band * fund * scale,
         "buy_and_hold": pd.Series(1.0, index=sig.index)}

    days = sig.index
    in_win = (days >= START) & (days < END)
    report: dict = {"plan": "docs/research/r12-band-voltarget-plan.md @ 3f8a1c0", "window": [str(START.date()), str(END.date())],
                    "data": {"days_in_window": int(in_win.sum()), **{f"{k}_missing": int(v[in_win].isna().sum()) for k, v in w.items()}},
                    "days_scaled_while_in": int(((scale < 1) & (band > 0) & in_win).sum())}
    out = Path("reports/r12")
    out.mkdir(parents=True, exist_ok=True)
    runs: dict = {}
    for costs_name, costs in (("base_costs", RegimeCosts()), ("costs_x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        runs[costs_name] = {}
        for name, ws in w.items():
            eq, tr = simulate_weights(daily, ws, START, END, equity=EQUITY, costs=costs)
            r = metrics(eq) | {"trades": int(len(tr))}
            r["years"] = {y: metrics(eq[(eq.index >= pd.Timestamp(a) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b))]) for y, a, b in YEARS}
            if costs_name == "base_costs":
                r["episodes"] = episodes(eq)
                eq.to_csv(out / f"equity_{name}.csv")
            runs[costs_name][name] = r
    report["runs"] = runs
    b, r, bh = runs["base_costs"]["R10a_band"], runs["base_costs"]["R12_band_vol"], runs["base_costs"]["buy_and_hold"]
    report["verdict"] = {
        "better_than_R10a": bool(r["sharpe"] > b["sharpe"] and r["max_dd"] <= b["max_dd"] and r["net_return"] > 0),
        "drawdown_first": bool(r["max_dd"] <= b["max_dd"] - 0.05 and r["sharpe"] >= b["sharpe"] - 0.05 and r["net_return"] > 0),
        "note_objective_vs_bh": bool(r["net_return"] > 0 and (r["sharpe"] > bh["sharpe"] or r["max_dd"] <= 0.5 * bh["max_dd"])),
    }
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
