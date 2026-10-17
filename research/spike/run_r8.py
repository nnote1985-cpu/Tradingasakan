"""R8: order-book depth / open-interest filter on path A, pre-declared in
docs/research/r8-depth-oi-filter-plan.md (commit 2ba49d1). Run once; nothing is tuned afterwards.

    uv run python research/spike/download_data.py --intervals 1h
    uv run python research/spike/download_depth.py
    uv run python research/spike/run_r8.py
"""

from __future__ import annotations

import io
import json
import zipfile
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
START, END = pd.Timestamp("2023-04-01"), pd.Timestamp("2026-01-01")
YEARS = [("2023 (Apr-Dec)", "2023-04-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]
DEPTH_DIR = Path("data/binance_um_depth")
MIN_SNAPSHOTS, WINDOW, MIN_VALID = 100, 90, 60


def _csv(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        return pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))


def depth_imbalance() -> pd.DataFrame:
    rows = []
    for p in sorted((DEPTH_DIR / "bookDepth").glob("*.zip")):
        day = pd.Timestamp(p.stem.rsplit("-bookDepth-", 1)[1])
        df = _csv(p)
        ts = pd.to_datetime(df["timestamp"])
        df = df[(ts >= day) & (ts < day + pd.Timedelta(days=1))]
        piv = df[df["percentage"].isin([-2, 2])].pivot_table(index="timestamp", columns="percentage", values="notional", aggfunc="last").dropna()
        n = len(piv)
        imb = float(((piv[-2] - piv[2]) / (piv[-2] + piv[2])).mean()) if n >= MIN_SNAPSHOTS else np.nan
        rows.append({"day": day, "imb": imb, "snapshots": n})
    return pd.DataFrame(rows).set_index("day")


def open_interest() -> pd.Series:
    out = {}
    for p in sorted((DEPTH_DIR / "metrics").glob("*.zip")):
        day = pd.Timestamp(p.stem.rsplit("-metrics-", 1)[1])
        df = _csv(p)
        ts = pd.to_datetime(df["create_time"])
        df = df[(ts >= day) & (ts < day + pd.Timedelta(days=1))].sort_values("create_time")
        out[day] = float(df["sum_open_interest_value"].iloc[-1]) if len(df) else np.nan
    return pd.Series(out, name="oi")


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

    dep, oi = depth_imbalance(), open_interest()
    days = sig.index
    imb = dep["imb"].reindex(days)
    oi7 = oi.reindex(days) / oi.reindex(days).shift(7) - 1
    lo_imb = imb.rolling(WINDOW, min_periods=MIN_VALID).quantile(0.2)
    hi_oi = oi7.rolling(WINDOW, min_periods=MIN_VALID).quantile(0.8)
    thin = (imb < lo_imb).fillna(False)          # NaN (missing data or threshold) -> neutral
    oi_up = (oi7 > hi_oi).fillna(False)
    w = {"pathA": base, "R8a_depth": base.where(~thin, base * 0.5), "R8b_depth_oi": base.where(~(thin & oi_up), base * 0.5)}
    w["buy_and_hold"] = pd.Series(1.0, index=days)

    report: dict = {"plan": "docs/research/r8-depth-oi-filter-plan.md @ 2ba49d1", "window": [str(START.date()), str(END.date())]}
    in_win = (days >= START) & (days < END)
    report["data"] = {
        "depth_days_missing_in_window": int(imb[in_win].isna().sum()), "oi_days_missing_in_window": int(oi7[in_win].isna().sum()),
        "depth_files": int(len(dep)), "median_snapshots_per_day": float(dep["snapshots"].median()),
        "days_in_window": int(in_win.sum()),
    }
    report["filtered_days"] = {"R8a_depth": int((thin & in_win & (base > 0)).sum()), "R8b_depth_oi": int((thin & oi_up & in_win & (base > 0)).sum()),
                               "thin_any_weight": int((thin & in_win).sum())}
    out = Path("reports/r8")
    out.mkdir(parents=True, exist_ok=True)
    runs: dict = {}
    for costs_name, costs in (("base_costs", RegimeCosts()), ("costs_x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        runs[costs_name] = {}
        for name, ws in w.items():
            eq, tr = simulate_weights(daily, ws.fillna(1.0) if name == "buy_and_hold" else ws, START, END, equity=EQUITY, costs=costs)
            r = metrics(eq) | {"trades": int(len(tr))}
            r["years"] = {y: metrics(eq[(eq.index >= pd.Timestamp(a) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b))]) for y, a, b in YEARS}
            runs[costs_name][name] = r
            if costs_name == "base_costs":
                eq.to_csv(out / f"equity_{name}.csv")
    report["runs"] = runs
    b, bh = runs["base_costs"]["pathA"], runs["base_costs"]["buy_and_hold"]
    report["verdict"] = {}
    for v in ("R8a_depth", "R8b_depth_oi"):
        r = runs["base_costs"][v]
        c1 = r["sharpe"] > b["sharpe"] and r["max_dd"] <= b["max_dd"] and r["net_return"] > 0
        obj = r["net_return"] > 0 and (r["sharpe"] > bh["sharpe"] or r["max_dd"] <= 0.5 * bh["max_dd"])
        report["verdict"][v] = {"better_than_pathA": c1, "note_objective_vs_bh": obj}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    feats = {"imb": imb, "lo_imb": lo_imb, "oi7": oi7, "hi_oi": hi_oi, "thin": thin, "oi_up": oi_up}
    pd.DataFrame(feats | {f"w_{k}": v for k, v in w.items()}).to_csv(out / "daily_features.csv")
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
