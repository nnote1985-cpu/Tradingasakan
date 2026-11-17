"""R20: layer 1 = R10a on top of the R19 carry base, pre-declared in docs/research/r20-layer1-r10a-plan.md
(commit 0a8bcfc). Sizes 1.0 / 0.5 / 0.25, dev and C1.

    PYTHONPATH=research/spike uv run python research/spike/run_r20.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
from common import jsonable
from run_r19 import quarterly

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe

OUT = Path("/mnt/project-files/r20-results")
SIZES = (1.0, 0.5, 0.25)


def run(daily, spot, fd, ws, wp, start, end):
    log: list = []
    eq, _, _, st = run_r15.simulate(daily, spot, fd, ws, wp, start, end, run_r15.Costs(), daily_log=log)
    return eq, quarterly(pd.DataFrame(log), eq), st


def main() -> None:
    np.seterr(all="ignore")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rep: dict = {"plan": "docs/research/r20-layer1-r10a-plan.md @ 0a8bcfc", "commit": commit, "windows": {}}
    for wn in ("dev", "c1"):
        win = run_r15.WINDOWS[wn]
        start, end = win["range"]
        daily, spot, fd, _ = run_r15.market(win)
        _, feats = run_r15.targets(daily, fd)
        w = feats["r10a_w"]
        one = pd.Series(1.0, index=w.index)
        (OUT / wn).mkdir(parents=True, exist_ok=True)
        beq, bq, _ = run(daily, spot, fd, one * 0.5, one * -0.5, start, end)
        base = {"net": float(beq.iloc[-1] / beq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(beq)), "max_dd": max_drawdown(beq)}
        res = {"base_K0": base}
        for s in SIZES:
            u = (s * w).where(w.notna())
            eq, q, st = run(daily, spot, fd, u + 0.5 * (1 - u), -0.5 * (1 - u), start, end)
            yrs = (eq.index[-1] - eq.index[0]).days / 365
            net = float(eq.iloc[-1] / eq.iloc[0] - 1)
            full = q[q["days"] >= 30]
            e = eq.iloc[1:]
            start_eq = eq.shift(1).iloc[1:].groupby(e.index.to_period("Q")).first()
            start_eq.index = start_eq.index.astype(str)
            contrib = ((q["pnl"] - bq["pnl"]) / start_eq * 100).round(2)
            table = pd.DataFrame({"base_pnl_pct": bq["pnl_pct"], "combined_pnl_pct": q["pnl_pct"], "layer_contrib_pct": contrib})
            table.to_csv(OUT / wn / f"quarters_s{s}.csv")
            eq.to_csv(OUT / wn / f"equity_s{s}.csv")
            r = {"net": net, "annual": float((1 + net) ** (1 / yrs) - 1), "sharpe": sharpe(daily_log_returns(eq)),
                 "max_dd": max_drawdown(eq), "quarters": int(len(full)), "quarters_non_negative": int((full["pnl"] >= 0).sum()),
                 "worst_quarter_pct": float(full["pnl_pct"].min()), "worst_layer_contrib_pct": float(contrib[full.index].min()),
                 "liquidated": st["liquidated"]}
            r["c1_beats_base"] = bool(net > base["net"] and r["sharpe"] > base["sharpe"])
            r["c2_layer_loss_ok"] = bool(r["worst_layer_contrib_pct"] >= -5.0)
            r["c3_quarters_dd"] = bool(r["quarters_non_negative"] / r["quarters"] >= 0.7 and r["max_dd"] <= 0.15)
            r["pass"] = bool(r["c1_beats_base"] and r["c2_layer_loss_ok"] and r["c3_quarters_dd"] and st["liquidated"] is None)
            res[f"s{s}"] = r
        bq.to_csv(OUT / wn / "quarters_base.csv")
        rep["windows"][wn] = {"range": [start, end], "runs": res}
    (OUT / "report.json").write_text(json.dumps(jsonable(rep), indent=2))
    for wn, wv in rep["windows"].items():
        b = wv["runs"]["base_K0"]
        print(wn, wv["range"], f"base net {b['net']:+.1%} sh {b['sharpe']:.2f} dd {b['max_dd']:.1%}")
        for k, r in wv["runs"].items():
            if k == "base_K0":
                continue
            print(f"  {k}: net {r['net']:+.1%} ({r['annual']:+.1%}/yr) sh {r['sharpe']:.2f} dd {r['max_dd']:.1%} "
                  f"q+ {r['quarters_non_negative']}/{r['quarters']} worstQ {r['worst_quarter_pct']:.1f}% "
                  f"worst layer {r['worst_layer_contrib_pct']:.1f}% | 1:{r['c1_beats_base']} 2:{r['c2_layer_loss_ok']} "
                  f"3:{r['c3_quarters_dd']} {'PASS' if r['pass'] else 'fail'}")


if __name__ == "__main__":
    main()
