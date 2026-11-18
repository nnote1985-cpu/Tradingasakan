"""R22: sharper hedge timing for 3a, pre-declared in docs/research/r22-sharper-hedge-plan.md (commit 6a31062).

    PYTHONPATH=research/spike uv run python research/spike/run_r22.py btc    # select on BTC dev + C1
    PYTHONPATH=research/spike uv run python research/spike/run_r22.py eth    # one run: chosen vs H0 on ETH-1F
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
import run_r21
from common import jsonable
from run_r10 import BAND, latch

OUT = Path("/mnt/project-files/r22-results")
NAMES = ("H0_current", "H1_asym_exit", "H2_sma50", "H3_two_stage", "H4_dd_hedge")


def unhedged(feats: pd.DataFrame, daily: pd.DataFrame) -> dict[str, pd.Series]:
    c = feats["close"]
    sma = feats["sma100"]
    fund = feats["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    h0 = feats["band"]
    h1 = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i])
    sma50 = c.rolling(50, min_periods=50).mean()
    h2 = latch(c, sma50, lambda i: c.iloc[i] > sma50.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma50.iloc[i] * (1 - BAND))
    ema20 = c.ffill().ewm(span=20, adjust=False, min_periods=20).mean()
    h3 = h0 * np.where(c < ema20, 0.5, 1.0)
    hi20 = c.ffill().rolling(20, min_periods=20).max()
    prev_hi = hi20.shift(1)
    dd_state, out = False, np.full(len(c), np.nan)
    for i in range(len(c)):
        b = h0.iloc[i]
        if not np.isfinite(b):
            continue
        if b == 0:
            dd_state = False
        elif not dd_state and np.isfinite(hi20.iloc[i]) and c.iloc[i] <= hi20.iloc[i] * 0.90:
            dd_state = True
        elif dd_state and np.isfinite(prev_hi.iloc[i]) and c.iloc[i] > prev_hi.iloc[i]:
            dd_state = False
        out[i] = 0.0 if (b == 1 and dd_state) else b
    h4 = pd.Series(out, index=c.index)
    states = {"H0_current": h0, "H1_asym_exit": h1, "H2_sma50": h2, "H3_two_stage": h3, "H4_dd_hedge": h4}
    return {k: (v * fund).where(v.notna()) for k, v in states.items()}


def run_window(win: dict, names) -> tuple[dict, dict]:
    start, end = win["range"]
    daily, spot, fd, _ = run_r15.market(win)
    _, feats = run_r15.targets(daily, fd)
    us = unhedged(feats, daily)
    res, curves = {}, {}
    for n in names:
        u = us[n]
        eq = run_r15.simulate(daily, spot, fd, u + 0.5 * (1 - u), -0.5 * (1 - u), start, end, run_r15.Costs())[0]
        st = run_r21.stats(eq)
        inwin = u[(u.index >= start) & (u.index < end)]
        st["hedge_switches"] = int((inwin.diff().abs() > 0).sum())
        res[n], curves[n] = st, eq
    return res, curves


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("btc", "eth"))
    a = ap.parse_args()
    np.seterr(all="ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if a.stage == "btc":
        rep: dict = {"plan": "docs/research/r22-sharper-hedge-plan.md @ 6a31062", "commit": commit, "windows": {}}
        for wn in ("dev", "c1"):
            res, curves = run_window(run_r15.WINDOWS[wn], NAMES)
            rep["windows"][wn] = res
            pd.DataFrame(curves).to_csv(OUT / f"equity_btc_{wn}.csv")
        d, c = rep["windows"]["dev"], rep["windows"]["c1"]
        score = {n: (d[n]["sharpe"] + c[n]["sharpe"]) / 2 for n in NAMES}
        ok = [n for n in NAMES if n != "H0_current" and d[n]["max_dd"] <= d["H0_current"]["max_dd"] + 0.02
              and c[n]["max_dd"] <= c["H0_current"]["max_dd"] + 0.02 and score[n] > score["H0_current"]]
        rep["score_mean_sharpe"] = score
        rep["chosen"] = max(ok, key=lambda n: score[n]) if ok else "H0_current"
        (OUT / "btc_report.json").write_text(json.dumps(jsonable(rep), indent=2))
        for wn, res in rep["windows"].items():
            print(wn)
            for n, s in res.items():
                print(f"  {n:14s} {s['annual']:+.0%}/yr dd {s['max_dd']:.1%} sh {s['sharpe']:.2f} Q+ {s['quarters_non_negative']} "
                      f"Y+ {s['years_non_negative']} switches {s['hedge_switches']}")
        print("score", {k: round(v, 3) for k, v in score.items()}, "chosen", rep["chosen"])
    else:
        btc = json.loads((OUT / "btc_report.json").read_text())
        chosen = btc["chosen"]
        if (OUT / "eth_report.json").exists():
            raise SystemExit("ETH-1F already used for R22")
        if chosen == "H0_current":
            raise SystemExit("nothing beat H0 on BTC; ETH not run")
        run_r15.SYMBOL = run_r21.SYM
        res, curves = run_window(run_r21.WIN_F, ("H0_current", chosen))
        pd.DataFrame(curves).to_csv(OUT / "equity_eth1f.csv")
        rep = {"chosen": chosen, "commit": commit, "eth_1f": res}
        h0, ch = res["H0_current"], res[chosen]
        rep["better_on_eth"] = bool(ch["sharpe"] > h0["sharpe"] and ch["max_dd"] <= h0["max_dd"] + 0.02)
        (OUT / "eth_report.json").write_text(json.dumps(jsonable(rep), indent=2))
        for n, s in res.items():
            print(f"  {n:14s} {s['annual']:+.0%}/yr dd {s['max_dd']:.1%} sh {s['sharpe']:.2f} Q+ {s['quarters_non_negative']} "
                  f"Y+ {s['years_non_negative']} switches {s['hedge_switches']} years {s['years']}")
        print("better_on_eth", rep["better_on_eth"])


if __name__ == "__main__":
    main()
