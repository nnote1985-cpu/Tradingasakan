"""R9: realised-volatility filter on path A, pre-declared in
docs/research/r9-volatility-filter-plan.md (commit a1784ec). Run once; nothing is tuned afterwards.

    uv run python research/spike/download_data.py --intervals 1h
    PYTHONPATH=research/spike uv run python research/spike/run_r9.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable, load_config

from trading.alerts.risk_check import HIGH_VOL, signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

EQUITY = 10_000.0
START, END = pd.Timestamp("2022-07-01"), pd.Timestamp("2026-01-01")
YEARS = [("2022 (Jul-Dec)", "2022-07-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
         ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]
MED_WINDOW, MED_MIN = 365, 180


def metrics(eq: pd.Series) -> dict:
    return {"net_return": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}


def main() -> None:
    cfg = load_config("configs/spike.json")
    d = Path(cfg["data_dir"])
    h1 = load(d, "BTCUSDT", "1h")
    daily = daily_bars(h1)
    daily.index = daily.index.tz_localize(None) if daily.index.tz is not None else daily.index
    f = load_funding(d, "BTCUSDT")
    f.index = f.index.tz_localize(None) if f.index.tz is not None else f.index
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    base = sig["suggested_btc_weight"]
    days = sig.index

    vol = sig["vol_30d"]
    med = vol.rolling(MED_WINDOW, min_periods=MED_MIN).median()
    high = (vol > HIGH_VOL).fillna(False)                       # NaN -> neutral
    raw = (med / vol).clip(upper=1.0)
    scale = (np.floor(raw * 4) / 4).clip(lower=0.25).where(raw.notna(), 1.0)
    w = {"pathA": base, "R9a_cap": base.where(~high, base * 0.5), "R9b_target": base * scale}
    w["buy_and_hold"] = pd.Series(1.0, index=days)

    in_win = (days >= START) & (days < END)
    report: dict = {"plan": "docs/research/r9-volatility-filter-plan.md @ a1784ec", "window": [str(START.date()), str(END.date())]}
    report["data"] = {"days_in_window": int(in_win.sum()), "base_weight_missing_in_window": int(base[in_win].isna().sum()),
                      "vol_missing_in_window": int(vol[in_win].isna().sum()), "median_missing_in_window": int(med[in_win].isna().sum())}
    report["filtered_days"] = {"R9a_cap": int((high & in_win & (base > 0)).sum()),
                               "R9b_target": int(((scale < 1) & in_win & (base > 0)).sum()),
                               "vol_above_0.80_any_weight": int((high & in_win).sum())}
    report["scale_distribution_R9b_in_window"] = {str(k): int(v) for k, v in scale[in_win].value_counts().sort_index().items()}
    out = Path("reports/r9")
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
    for v in ("R9a_cap", "R9b_target"):
        r = runs["base_costs"][v]
        c1 = r["sharpe"] > b["sharpe"] and r["max_dd"] <= b["max_dd"] and r["net_return"] > 0
        obj = r["net_return"] > 0 and (r["sharpe"] > bh["sharpe"] or r["max_dd"] <= 0.5 * bh["max_dd"])
        report["verdict"][v] = {"better_than_pathA": c1, "note_objective_vs_bh": obj}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    feats = {"vol_30d": vol, "median_365d": med, "high": high, "scale": scale}
    pd.DataFrame(feats | {f"w_{k}": v for k, v in w.items()}).to_csv(out / "daily_features.csv")
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
