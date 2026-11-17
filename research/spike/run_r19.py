"""R19: delta-neutral funding carry as a non-losing base, pre-declared in docs/research/r19-carry-base-plan.md
(commit 2d29a0d). Three variants, dev and C1.

    PYTHONPATH=research/spike uv run python research/spike/run_r19.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe

OUT = Path("/mnt/project-files/r19-results")


def quarterly(log: pd.DataFrame, eq: pd.Series) -> pd.DataFrame:
    df = log.set_index("day")
    df["pnl"] = df["equity"].diff()
    df.iloc[0, df.columns.get_loc("pnl")] = df["equity"].iloc[0] - run_r15.EQUITY
    q = df.groupby(df.index.to_period("Q")).agg(days=("pnl", "size"), pnl=("pnl", "sum"), fees=("fees", "sum"),
                                                 funding_received=("funding_paid", lambda s: -s.sum()),
                                                 days_on=("spot_qty", lambda s: int((s > 0).sum())))
    start_eq = df["equity"].shift(1).fillna(run_r15.EQUITY).groupby(df.index.to_period("Q")).first()
    q["pnl_pct"] = q["pnl"] / start_eq * 100
    q["basis_and_other"] = q["pnl"] + q["fees"] - q["funding_received"]
    q.index = q.index.astype(str)
    return q.round(2)


def main() -> None:
    np.seterr(all="ignore")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report: dict = {"plan": "docs/research/r19-carry-base-plan.md @ 2d29a0d", "commit": commit, "windows": {}}
    for wn in ("dev", "c1"):
        win = run_r15.WINDOWS[wn]
        start, end = win["range"]
        daily, spot, fd, _ = run_r15.market(win)
        tg, feats = run_r15.targets(daily, fd)
        carry = feats["carry_on"]
        one = pd.Series(1.0, index=carry.index)
        variants = {"K0_always": (one * 0.5, one * -0.5), "K1_filter": (carry * 0.5, carry * -0.5),
                    "K2_filter_3x": (carry * 0.75, carry * -0.75)}
        wres = {}
        (OUT / wn).mkdir(parents=True, exist_ok=True)
        for name, (ws, wp) in variants.items():
            log: list = []
            eq, weq, trades, st = run_r15.simulate(daily, spot, fd, ws, wp, start, end, run_r15.Costs(), daily_log=log)
            q = quarterly(pd.DataFrame(log), eq)
            q.to_csv(OUT / wn / f"quarters_{name}.csv")
            full = q[q["days"] >= 30]
            yrs = (eq.index[-1] - eq.index[0]).days / 365
            net = float(eq.iloc[-1] / eq.iloc[0] - 1)
            peak = eq.cummax()
            r = {"net": net, "annual_yield": float((1 + net) ** (1 / yrs) - 1), "sharpe": sharpe(daily_log_returns(eq)),
                 "max_dd": max_drawdown(eq), "max_dd_intraday": float(((peak - weq.reindex(eq.index).fillna(eq)) / peak).max()),
                 "quarters": int(len(full)), "quarters_non_negative": int((full["pnl"] >= 0).sum()),
                 "losing_quarters": full[full["pnl"] < 0].to_dict(orient="index"),
                 "fees": st["fees"], "funding_received": -st["funding_paid"], "liquidated": st["liquidated"],
                 "trades": int(len(trades)), "days_on": int(sum(1 for x in log if x["spot_qty"] > 0)), "days": len(log)}
            r["share_quarters_non_negative"] = r["quarters_non_negative"] / max(r["quarters"], 1)
            r["pass"] = bool(r["share_quarters_non_negative"] >= 0.8 and r["max_dd"] <= 0.10 and net > 0 and st["liquidated"] is None)
            wres[name] = r
            eq.to_csv(OUT / wn / f"equity_{name}.csv")
        report["windows"][wn] = {"range": [start, end], "runs": wres}
    (OUT / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    for wn, w in report["windows"].items():
        print(wn, w["range"])
        for n, r in w["runs"].items():
            print(f"  {n:14s} net {r['net']:+.1%} yr {r['annual_yield']:+.1%} sh {r['sharpe']:.1f} dd {r['max_dd']:.1%} "
                  f"idd {r['max_dd_intraday']:.1%} q+ {r['quarters_non_negative']}/{r['quarters']} fund {r['funding_received']:.0f} "
                  f"fees {r['fees']:.0f} on {r['days_on']}/{r['days']} liq {r['liquidated']} {'PASS' if r['pass'] else 'fail'}")
            for k, v in r["losing_quarters"].items():
                print(f"      loss {k}: pnl {v['pnl']:.0f} ({v['pnl_pct']:.2f}%) fund {v['funding_received']:.0f} fees {v['fees']:.0f} "
                      f"basis {v['basis_and_other']:.0f} on {v['days_on']}d")


if __name__ == "__main__":
    main()
