"""R10: hysteresis band / 3-day confirmation on path A's trend rule, pre-declared in
docs/research/r10-trend-hysteresis-plan.md (commit 0930000). Run once; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r10.py
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable, load_config

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

EQUITY = 10_000.0
START, END = pd.Timestamp("2022-05-01"), pd.Timestamp("2026-01-01")
YEARS = [("2022 (May-Dec)", "2022-05-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
         ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]
BAND, CONFIRM = 0.02, 3


def latch(close: pd.Series, sma: pd.Series, go_in: Callable[[int], bool], go_out: Callable[[int], bool]) -> pd.Series:
    """Sticky trend state; NaN on days with no close/SMA (state carried through them)."""
    state: float | None = None
    out = np.full(len(close), np.nan)
    for i in range(len(close)):
        if not (np.isfinite(close.iloc[i]) and np.isfinite(sma.iloc[i])):
            continue
        if state is None:
            state = 1.0 if close.iloc[i] > sma.iloc[i] else 0.0
        elif state == 0.0 and go_in(i):
            state = 1.0
        elif state == 1.0 and go_out(i):
            state = 0.0
        out[i] = state
    return pd.Series(out, index=close.index)


def metrics(eq: pd.Series) -> dict:
    return {"net_return": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}


def main() -> None:
    cfg = load_config("configs/spike.json")
    d = Path(cfg["data_dir"])
    daily = daily_bars(load(d, "BTCUSDT", "1h"))
    daily.index = daily.index.tz_localize(None) if daily.index.tz is not None else daily.index
    f = load_funding(d, "BTCUSDT")
    f.index = f.index.tz_localize(None) if f.index.tz is not None else f.index
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    c, sma = sig["close"], sig["sma100"]
    above = (c > sma).to_numpy() & sma.notna().to_numpy()
    below = (c < sma).to_numpy() & sma.notna().to_numpy()
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)

    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND))
    conf = latch(c, sma, lambda i: i >= CONFIRM - 1 and bool(above[i - CONFIRM + 1:i + 1].all()),
                 lambda i: i >= CONFIRM - 1 and bool(below[i - CONFIRM + 1:i + 1].all()))
    base_state = sig["trend_up"].map({True: 1.0, False: 0.0}).astype(float)
    w = {"pathA": sig["suggested_btc_weight"], "R10a_band": band * fund, "R10b_confirm": conf * fund,
         "buy_and_hold": pd.Series(1.0, index=sig.index)}
    states = {"pathA": base_state, "R10a_band": band, "R10b_confirm": conf}

    days = sig.index
    in_win = (days >= START) & (days < END)
    report: dict = {"plan": "docs/research/r10-trend-hysteresis-plan.md @ 0930000", "window": [str(START.date()), str(END.date())]}
    report["data"] = {"days_in_window": int(in_win.sum()), **{f"{k}_weight_missing_in_window": int(v[in_win].isna().sum()) for k, v in w.items()}}
    report["state_changes"] = {k: {y: int((s[(days >= a) & (days < b)].diff().abs() > 0).sum()) for y, a, b in YEARS} for k, s in states.items()}
    report["days_in"] = {k: int((s[in_win] > 0).sum()) for k, s in states.items()}
    out = Path("reports/r10")
    out.mkdir(parents=True, exist_ok=True)
    runs: dict = {}
    for costs_name, costs in (("base_costs", RegimeCosts()), ("costs_x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        runs[costs_name] = {}
        for name, ws in w.items():
            eq, tr = simulate_weights(daily, ws, START, END, equity=EQUITY, costs=costs)
            r = metrics(eq) | {"trades": int(len(tr))}
            r["years"] = {y: metrics(eq[(eq.index >= pd.Timestamp(a) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b))]) for y, a, b in YEARS}
            runs[costs_name][name] = r
            if costs_name == "base_costs":
                eq.to_csv(out / f"equity_{name}.csv")
    report["runs"] = runs
    b, bh = runs["base_costs"]["pathA"], runs["base_costs"]["buy_and_hold"]
    report["verdict"] = {}
    for v in ("R10a_band", "R10b_confirm"):
        r = runs["base_costs"][v]
        c1 = r["sharpe"] > b["sharpe"] and r["max_dd"] <= b["max_dd"] and r["net_return"] > 0
        obj = r["net_return"] > 0 and (r["sharpe"] > bh["sharpe"] or r["max_dd"] <= 0.5 * bh["max_dd"])
        report["verdict"][v] = {"better_than_pathA": c1, "note_objective_vs_bh": obj}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    feats = {"close": c, "sma100": sma} | {f"state_{k}": v for k, v in states.items()}
    pd.DataFrame(feats | {f"w_{k}": v for k, v in w.items()}).to_csv(out / "daily_features.csv")
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
