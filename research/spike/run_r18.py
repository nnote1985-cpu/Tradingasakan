"""R18: SL/TP and sizing on R10a's long signal on the perp, pre-declared in
docs/research/r18-r10a-sl-tp-plan.md (commit 8df410a). 14 runs per window; nothing tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r18.py dev
    PYTHONPATH=research/spike uv run python research/spike/run_r18.py c1     # once, after dev is committed
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
from common import jsonable
from run_r17 import daily_features

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import RegimeCosts

FEE, SLIP, MAINT = 0.0005, 2.0 / 1e4, 0.005
R_ATR = 3.0
EXITS = ("E0_none", "E1_trail", "E2_tp2R", "E3_tp3R", "E4_half2R_trail")
SIZINGS = ("1x", "2x", "risk2pct")
YEARS = {"dev": [("2022 May-Dec", "2022-05-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
                 ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")],
         "c1": [("2020 Apr-Dec", "2020-04-15", "2021-01-01"), ("2021", "2021-01-01", "2022-01-01")]}


def signals(daily: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    df = daily_features(daily, feats)
    band = feats["band"].reindex(daily.index)
    c = daily["close"]
    df["band"] = band
    df["enter"] = (band == 1) & ((band.shift(1) == 0) | (c > c.ffill().rolling(20).max().shift(1)))
    df["exit"] = band == 0
    return df


def run(h1, fund, df, start, end, exit_rule: str, sizing: str, fee=FEE, slip=SLIP):
    bars = h1.loc[(h1.index >= pd.Timestamp(start, tz="UTC")) & (h1.index < pd.Timestamp(end, tz="UTC"))]
    cash, qty = 10_000.0, 0.0
    entry = stop = tp = trail_hi = rdist = np.nan
    half_done = False
    pend_in, pend_out = None, False
    marks, trades = {}, []
    st = {"fees": 0.0, "funding_paid": 0.0, "liquidated": None, "min_liq_dist_pct": None, "avg_sl_dist_pct": None}
    liq_d, sl_d = [], []
    last_c = np.nan

    def sell(q, px_raw, t, why):
        nonlocal cash, qty
        px = px_raw * (1 - slip)
        f = q * px * fee
        cash += q * (px - entry) - f
        st["fees"] += f
        qty -= q
        trades.append({"time": str(t), "why": why, "qty": q, "px": px, "pnl": q * (px - entry) - f})

    for t, row in bars.iterrows():
        o, hi, lo, c = row["open"], row["high"], row["low"], row["close"]
        if not np.isfinite(o):
            continue
        if qty and t in fund.index:
            pay = qty * o * float(fund.loc[t])
            cash -= pay
            st["funding_paid"] += pay
        if t.hour == 0:
            if pend_out and qty:
                sell(qty, o, t, "signal")
            if pend_in is not None and not qty:
                atr = pend_in
                eq = cash
                px = o * (1 + slip)
                rdist = R_ATR * atr
                lev = {"1x": 1.0, "2x": 2.0}.get(sizing) or min(0.02 / (rdist / px), 2.0)
                qty = lev * eq / px
                f = qty * px * fee
                cash -= f
                st["fees"] += f
                entry, trail_hi, half_done = px, px, False
                stop = px - rdist if exit_rule != "E0_none" else -np.inf
                tp = px + {"E2_tp2R": 2, "E3_tp3R": 3, "E4_half2R_trail": 2}.get(exit_rule, np.inf) * rdist
                liq = px * (1 - (eq - f) / (qty * px) + MAINT)
                liq_d.append((1 - liq / px) * 100)
                sl_d.append(rdist / px * 100)
                trades.append({"time": str(t), "why": "entry", "qty": qty, "px": px, "lev": lev, "pnl": -f})
            pend_in, pend_out = None, False
        if qty:
            # liquidation price p: cash + qty * (p - entry) = MAINT * qty * p
            liq = (qty * entry - cash) / (qty * (1 - MAINT))
            if lo <= liq and liq > stop:
                cash += qty * (liq - entry)
                st["liquidated"] = str(t)
                qty = 0.0
                marks[t.tz_localize(None).floor("1D")] = max(cash, 0.0)
                break
            if lo <= stop:
                sell(qty, min(o, stop), t, "SL" if stop <= entry - rdist + 1e-9 else "trail")
            elif hi >= tp:
                if exit_rule == "E4_half2R_trail" and not half_done:
                    sell(qty / 2, max(o, tp), t, "TP_half")
                    half_done, tp = True, np.inf
                else:
                    sell(qty, max(o, tp), t, "TP")
            if qty and exit_rule in ("E1_trail", "E4_half2R_trail"):
                trail_hi = max(trail_hi, hi)
                stop = max(stop, trail_hi - rdist)
        if np.isfinite(c):
            last_c = c
        if t.hour == 23:
            day = t.tz_localize(None).floor("1D")
            marks[day] = cash + (qty * (last_c - entry) if qty else 0.0)
            if day in df.index:
                x = df.loc[day]
                if qty and bool(x["exit"]):
                    pend_out = True
                elif not qty and bool(x["enter"]) and np.isfinite(x["atr14"]):
                    pend_in = float(x["atr14"])
    if qty:
        sell(qty, last_c, bars.index[-1], "end")
        marks[max(marks)] = cash
    eq = pd.Series(marks).sort_index()
    eq = pd.concat([pd.Series([10_000.0], index=[eq.index[0] - pd.Timedelta(days=1)]), eq])
    if liq_d:
        st["min_liq_dist_pct"], st["avg_sl_dist_pct"] = float(min(liq_d)), float(np.mean(sl_d))
    tr = pd.DataFrame(trades)
    st["entries"] = int((tr["why"] == "entry").sum()) if len(tr) else 0
    st["exits"] = tr.loc[tr["why"] != "entry", "why"].value_counts().to_dict() if len(tr) else {}
    return eq, tr, st


def met(eq):
    ok = bool((eq > 0).all())
    return {"net": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)) if ok else float("nan"),
            "max_dd": max_drawdown(eq)}


def per_year(eq, years):
    out = {}
    for y, a, b in years:
        e = eq[(eq.index >= pd.Timestamp(a) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b))]
        if len(e) > 2 and (e > 0).all():
            out[y] = {"net": float(e.iloc[-1] / e.iloc[0] - 1), "max_dd": max_drawdown(e)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("window", choices=("dev", "c1"))
    ap.add_argument("--out", default="/mnt/project-files/r18-results")
    a = ap.parse_args()
    win = run_r15.WINDOWS[a.window]
    start, end = win["range"]
    out = Path(a.out) / a.window
    if a.window == "c1":
        if not (Path(a.out) / "dev" / "report.json").exists():
            raise SystemExit("run dev first")
        if (out / "report.json").exists():
            raise SystemExit("C1 already run for R18")
    out.mkdir(parents=True, exist_ok=True)
    np.seterr(all="ignore")
    daily, spot, fd, _ = run_r15.market(win)
    _, feats = run_r15.targets(daily, fd)
    df = signals(daily, feats)
    raw = load(Path(win["perp_dir"]), "BTCUSDT", "1h")
    if raw.index.max() >= pd.Timestamp("2026-01-01", tz="UTC"):
        raise RuntimeError("C2 guard")
    h1 = regularize_h1(raw)
    f = load_funding(Path(win["perp_dir"]), "BTCUSDT")
    fund = f.groupby(f.index.floor("1h")).sum()
    years = YEARS[a.window]
    bh = run_r15.buy_and_hold(spot, start, end, run_r15.Costs())
    r10a, _ = simulate_weights(daily, feats["r10a_w"], pd.Timestamp(start), pd.Timestamp(end), equity=10_000.0, costs=RegimeCosts())
    bh_y, r10_y = per_year(bh, years), per_year(r10a, years)
    regime = {y: ("bull" if v["net"] > 0.2 else "bear" if v["net"] < -0.2 else "sideways") for y, v in bh_y.items()}
    bh2 = run_r15.buy_and_hold(spot, start, end, run_r15.Costs().x(2))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rep: dict = {"plan": "docs/research/r18-r10a-sl-tp-plan.md @ 8df410a", "commit": commit, "window": [start, end],
                 "regimes": regime, "buy_and_hold": met(bh) | {"years": bh_y}, "R10a_spot": met(r10a) | {"years": r10_y},
                 "runs": {}, "verdict": {}}
    R, B, B2 = rep["R10a_spot"], rep["buy_and_hold"], met(bh2)
    curves = {"buy_and_hold": bh, "R10a_spot": r10a}
    for ex in EXITS:
        for sz in SIZINGS:
            if ex == "E0_none" and sz == "risk2pct":
                continue
            name = f"{ex}|{sz}"
            eq, tr, st = run(h1, fund, df, start, end, ex, sz)
            eq2, _, st2 = run(h1, fund, df, start, end, ex, sz, FEE * 2, SLIP * 2)
            res = met(eq) | st | {"years": per_year(eq, years), "costs_x2": met(eq2) | {"liquidated": st2["liquidated"]}}
            rep["runs"][name] = res
            curves[name] = eq
            tr.to_csv(out / f"trades_{name.replace('|', '_')}.csv", index=False)
            okb = lambda m, b: bool(m["net"] > 0 and (m["sharpe"] > b["sharpe"] or m["max_dd"] <= 0.5 * b["max_dd"]))  # noqa: E731
            d_ok = True
            for y, reg in regime.items():
                yv = res["years"].get(y)
                if yv is None:
                    d_ok = False
                elif reg == "bull":
                    d_ok &= yv["net"] >= 0.5 * bh_y[y]["net"]
                else:
                    d_ok &= yv["net"] >= r10_y[y]["net"]
            v = {"A_beats_R10a": bool(res["sharpe"] > R["sharpe"] and res["max_dd"] <= R["max_dd"] + 0.05),
                 "B_beats_BH": okb(res, B), "C_costs_x2": okb(res["costs_x2"], B2),
                 "no_liquidation": st["liquidated"] is None and st2["liquidated"] is None, "D_every_market": bool(d_ok)}
            v["pass"] = all(v.values())
            rep["verdict"][name] = v
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(jsonable(rep), indent=2))
    print(a.window, "regimes", regime)
    print(f"  B&H   {B['net']:+.0%} {B['sharpe']:.2f} {B['max_dd']:.0%} | " + " ".join(f"{y}:{v['net']:+.0%}" for y, v in bh_y.items()))
    print(f"  R10a  {R['net']:+.0%} {R['sharpe']:.2f} {R['max_dd']:.0%} | " + " ".join(f"{y}:{v['net']:+.0%}" for y, v in r10_y.items()))
    for n, r in rep["runs"].items():
        v = rep["verdict"][n]
        yrs = " ".join(f"{y}:{x['net']:+.0%}/{x['max_dd']:.0%}" for y, x in r["years"].items())
        flags = " ".join(k[0] for k, b in v.items() if b and k != "pass") + (" PASS" if v["pass"] else "")
        print(f"  {n:24s} {r['net']:+6.0%} sh {r['sharpe']:.2f} dd {r['max_dd']:.0%} | {yrs} | n {r['entries']} {r['exits']} "
              f"liq {r['liquidated']} liqdist {r['min_liq_dist_pct']} sl {r['avg_sl_dist_pct']} x2 {r['costs_x2']['sharpe']:.2f} {flags}")


if __name__ == "__main__":
    main()
