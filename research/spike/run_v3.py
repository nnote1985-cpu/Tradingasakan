"""v3 funding overlay on a BTC core vs BTC buy & hold — pre-declared in
docs/research/v3-funding-overlay-plan.md.

    uv run python research/spike/run_v3.py [--out reports/v3]

Development and validation only; the holdout is never simulated.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
from common import jsonable, load_config, partitions
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import OverlayParams, funding_weights, simulate_weights
from trading.strategies.trend_regime_v2 import daily_bars

EQUITY = 10_000.0


def metrics(eq: pd.Series) -> dict:
    r = daily_log_returns(eq)
    return {"net_return": float(eq.iloc[-1] / EQUITY - 1), "sharpe": sharpe(r), "max_dd": max_drawdown(eq)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/v3")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    d = Path(cfg["data_dir"])
    h1 = load(d, "BTCUSDT", "1h")
    daily = daily_bars(h1)
    f = load_funding(d, "BTCUSDT")
    funding_daily = f.groupby(f.index.floor("1D")).sum()
    parts = partitions(h1, cfg["partitions"], pd.Timedelta(hours=4 * 600))
    parts = {k: v for k, v in parts.items() if k != "holdout"}

    variants = {"v3": OverlayParams()}
    for t in (0.0006, 0.0012):
        variants[f"diag_threshold_{t}"] = replace(OverlayParams(), threshold_per_day=t)
    for w in (0.0, 0.75):
        variants[f"diag_weight_{w}"] = replace(OverlayParams(), elevated_weight=w)

    report = {"partitions": {k: [str(a), str(b)] for k, (a, b) in parts.items()},
              "holdout_simulated": False, "runs": {}}
    for part, (s, e) in parts.items():
        bh, _ = simulate_weights(daily, pd.Series(1.0, index=daily.index), s, e, equity=EQUITY)
        report["runs"][f"buy_and_hold/{part}"] = metrics(bh)
        bh.to_csv(out / f"equity_buy_and_hold_{part}.csv")
        for name, p in variants.items():
            w = funding_weights(funding_daily, daily.index, p)
            eq, tr = simulate_weights(daily, w, s, e, equity=EQUITY)
            days = eq.index[1:]
            report["runs"][f"{name}/{part}"] = {
                **metrics(eq), "trades": int(len(tr)),
                "share_days_reduced": float((w.shift(1).reindex(days).fillna(1.0) < 1.0).mean()),
            }
            if name == "v3":
                eq.to_csv(out / f"equity_v3_{part}.csv")
                tr.to_csv(out / f"trades_v3_{part}.csv", index=False)

    verdict = {}
    for part in parts:
        v, b = report["runs"][f"v3/{part}"], report["runs"][f"buy_and_hold/{part}"]
        verdict[part] = {"net_positive": v["net_return"] > 0, "sharpe_above_bh": v["sharpe"] > b["sharpe"],
                         "dd_at_most_half_bh": v["max_dd"] <= 0.5 * b["max_dd"],
                         "dd_below_bh": v["max_dd"] < b["max_dd"]}
        verdict[part]["pass"] = verdict[part]["net_positive"] and (
            verdict[part]["sharpe_above_bh"] or verdict[part]["dd_at_most_half_bh"])
        verdict[part]["weak_pass"] = verdict[part]["sharpe_above_bh"] and verdict[part]["dd_below_bh"]
    verdict["PASS"] = all(verdict[p]["pass"] for p in parts)
    verdict["WEAK_PASS"] = all(verdict[p]["weak_pass"] for p in parts)
    report["verdict"] = verdict
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(pd.DataFrame(report["runs"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    print(json.dumps(jsonable(verdict), indent=2))


if __name__ == "__main__":
    main()
