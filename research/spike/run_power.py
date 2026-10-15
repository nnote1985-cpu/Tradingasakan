"""Phase 0.5 power / design simulation on development data (BUILDPLAN v4.2 §17.3).

    uv run python research/spike/run_power.py [--config configs/spike.json] [--out reports/spike]

The decision rule below is fixed before seeing results:
  INSUFFICIENT_DATA  development has fewer than MIN_CANDIDATES labelled candidates,
                     or no evaluation duration fits inside development.
  CONTINUE           NULL_RANDOM passes at most NULL_MAX_PASS_RATE of runs (method is
                     not fooled by noise) and ORACLE_MODERATE passes at least
                     MIN_POWER of runs at some tested duration.
  REVISE_HYPOTHESIS  otherwise: even a moderately good filter could not be told
                     apart from v1 at the durations we can afford.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from common import jsonable, load_config, load_market, make_backtester, partitions

from trading.evaluation.power import default_filters, filter_utility, run_power_design
from trading.simulator.bar_backtest import BacktestConfig
from trading.strategies.trend_breakout_v1 import StrategyParams, build_features

MIN_CANDIDATES = 100
NULL_MAX_PASS_RATE = 0.10
MIN_POWER = 0.80


def decide(summary, labels) -> dict:
    n = int(labels["r_multiple"].notna().sum()) if len(labels) else 0
    if n < MIN_CANDIDATES or summary.empty:
        return {"decision": "INSUFFICIENT_DATA", "labelled_candidates": n}
    by = summary.set_index(["duration_days", "filter"])["pass_rate"]
    durations = sorted(summary["duration_days"].unique())
    null_ok = all(by.get((d, "null_random"), 1.0) <= NULL_MAX_PASS_RATE for d in durations)
    powered = [d for d in durations if by.get((d, "oracle_moderate"), 0.0) >= MIN_POWER]
    decision = "CONTINUE" if null_ok and powered else "REVISE_HYPOTHESIS"
    return {"decision": decision, "labelled_candidates": n, "null_false_pass_ok": null_ok,
            "min_powered_duration_days": min(powered) if powered else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/spike")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    market = load_market(cfg)
    params = StrategyParams()
    features = build_features(market["h1"], params)
    parts = partitions(market["h1"], cfg["partitions"], pd.Timedelta(hours=4 * params.warmup_4h_bars))
    dev_start, dev_end = parts["development"]
    bt = make_backtester(market, config=BacktestConfig(initial_equity=float(cfg["initial_equity"])),
                         features=features)

    p = cfg["power"]
    res = run_power_design(bt, dev_start=dev_start, dev_end=dev_end,
                           durations_days=tuple(p["durations_days"]), n_windows=p["n_windows"],
                           seeds=p["seeds"], alpha=p["alpha"], delta_floor=p["delta_floor"],
                           block_days=p["block_days"], reps=p["reps"])
    res["runs"].to_csv(out / "power_runs.csv", index=False)
    res["summary"].to_csv(out / "power_summary.csv", index=False)
    res["labels"].to_csv(out / "standalone_labels.csv", index=False)
    utility = {f.name: filter_utility(res["labels"], f) for f in default_filters()} if len(res["labels"]) else {}
    verdict = decide(res["summary"], res["labels"])
    (out / "power.json").write_text(json.dumps(jsonable({
        "settings": res.get("settings"), "filter_utility": utility, "verdict": verdict,
        "rule": {"MIN_CANDIDATES": MIN_CANDIDATES, "NULL_MAX_PASS_RATE": NULL_MAX_PASS_RATE,
                 "MIN_POWER": MIN_POWER},
    }), indent=2))
    if not res["summary"].empty:
        print(res["summary"].to_string())
    print(json.dumps(jsonable(verdict), indent=2))


if __name__ == "__main__":
    main()
