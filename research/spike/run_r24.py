"""R24: Note's 4H-regime / 1H-pullback short, pre-declared in docs/research/r24-pullback-short-plan.md (abbb5af).

    PYTHONPATH=research/spike uv run python research/spike/run_r24.py

BTC only (2018-04 -> 2025, spot 1h before 2020, perp after). ETH-2 / C2 / 2026 never loaded.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r23
from common import jsonable

FEE, SLIP, MAINT, RISK, DAY_CAP, HALT, EQ0 = 0.0005, 2.0 / 1e4, 0.005, 0.01, 0.03, 0.10, 10_000.0
START, END = "2018-04-01", "2026-01-01"
OUT = Path("/mnt/project-files/r24-results")


def wilder(x: pd.Series, n: int = 14) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def features(h1: pd.DataFrame) -> pd.DataFrame:
    o, h, lo, c = (h1[k] for k in ("open", "high", "low", "close"))
    # 4H regime from complete 4H bars (all 4 hours present), known from the 4H bar's close on
    g = h1.resample("4h", label="left", closed="left")
    h4 = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                       "n": g["close"].count()})
    h4 = h4[h4["n"] == 4]
    up, dn = h4["high"].diff(), -h4["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h4.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h4.index)
    tr4 = pd.concat([h4["high"] - h4["low"], (h4["high"] - h4["close"].shift()).abs(),
                     (h4["low"] - h4["close"].shift()).abs()], axis=1).max(axis=1)
    atr4 = wilder(tr4)
    pdi, mdi = 100 * wilder(pdm) / atr4, 100 * wilder(mdm) / atr4
    adx = wilder(100 * (pdi - mdi).abs() / (pdi + mdi))
    reg = pd.DataFrame({"adx": adx, "pdi": pdi, "mdi": mdi})
    reg.index = reg.index + pd.Timedelta(hours=3)          # stamp on the last 1h bar of the 4H bar
    reg = reg.reindex(h1.index).ffill(limit=4)
    f = pd.DataFrame(index=h1.index)
    f["regime"] = (reg["adx"] > 25) & (reg["mdi"] > reg["pdi"])
    f["di_flip"] = reg["mdi"] < reg["pdi"]
    f["new4h"] = h1.index.hour % 4 == 3
    ema = c.ewm(span=20, adjust=False, min_periods=20).mean()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    d = c.diff()
    rsi = 100 - 100 / (1 + wilder(d.clip(lower=0)) / wilder((-d).clip(lower=0)))
    below = (c < ema).astype(int)
    run3 = below.rolling(3).sum() == 3
    had_run = run3.shift(1).rolling(24, min_periods=1).max().fillna(0).astype(bool)   # 24 bars before signal bar
    f["signal"] = (f["regime"] & had_run & (h >= ema - 0.3 * atr) & (c < ema) & (c < o)
                   & (rsi >= 35) & (rsi <= 55) & (ema - c <= atr)).fillna(False)
    f["atr"], f["swing"] = atr, h.rolling(10).max()
    return f


def simulate(h1, fund, f, tp_r, regime_exit, cap, cost_mult=1.0, tp_first=False):
    fee, slip = FEE * cost_mult, SLIP * cost_mult
    sel = (h1.index >= pd.Timestamp(START, tz="UTC")) & (h1.index < pd.Timestamp(END, tz="UTC"))
    b = h1.loc[sel]
    o, hi, lo, c = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig, atr, sw = f.loc[sel, "signal"].to_numpy(), f.loc[sel, "atr"].to_numpy(), f.loc[sel, "swing"].to_numpy()
    flip, new4 = f.loc[sel, "di_flip"].fillna(False).to_numpy(), f.loc[sel, "new4h"].to_numpy()
    rate, hours, idx = fund.loc[sel].to_numpy(), b.index.hour.to_numpy(), b.index
    cash, qty, entry, sl, tp = EQ0, 0.0, 0.0, 0.0, 0.0
    peak, halted, day_start, last_c, blocked = EQ0, None, EQ0, np.nan, False
    pend, pend_exit = None, False
    trades, marks = [], {}
    st = {"fees": 0.0, "funding": 0.0, "cap_bound": 0, "daycap_hits": 0, "halted": None, "liquidated": None,
          "both_touched": 0, "hours_in_market": 0}

    def close(raw, i, why):
        nonlocal cash, qty
        px = raw * (1 + slip)
        fe = qty * px * fee
        cash += qty * (entry - px) - fe
        st["fees"] += fe
        trades[-1].update({"exit_time": str(idx[i]), "why": why, "pnl_pct": (cash / trades[-1]["eq_in"] - 1) * 100,
                           "hours": (idx[i] - pd.Timestamp(trades[-1]["entry_time"])) / pd.Timedelta(hours=1)})
        qty = 0.0

    for i in range(len(b)):
        if hours[i] == 0:
            day_start, blocked = cash + (qty * (entry - last_c) if qty else 0.0), False
        if not np.isfinite(o[i]):
            pend = None
            continue
        if qty and rate[i]:
            r = qty * o[i] * rate[i]
            cash += r
            st["funding"] += r
        if pend_exit and qty:
            close(o[i], i, "regime")
        pend_exit = False
        if pend is not None and not qty and halted is None and not blocked:
            a_, swing = pend
            px = o[i] * (1 - slip)
            dist = max(1.5 * a_, swing + 0.2 * a_ - px)
            nf = RISK / (dist / px)
            if nf > cap:
                nf, st["cap_bound"] = cap, st["cap_bound"] + 1
            eq_in = cash
            qty = nf * cash / px
            fe = qty * px * fee
            cash -= fe
            st["fees"] += fe
            entry, sl, tp = px, px + dist, px - tp_r * dist
            trades.append({"entry_time": str(idx[i]), "entry": px, "sl_dist_pct": dist / px * 100, "notional_x": nf,
                           "eq_in": eq_in})
        pend = None
        if qty:
            st["hours_in_market"] += 1
            hit_sl, hit_tp = hi[i] >= sl, lo[i] <= tp
            if hit_sl and hit_tp:
                st["both_touched"] += 1
            if hit_sl and not (tp_first and hit_tp):
                close(max(o[i], sl), i, "SL")
            elif hit_tp:
                close(min(o[i], tp), i, "TP")
        if qty and cash + qty * (entry - hi[i]) <= MAINT * qty * hi[i]:
            close(hi[i], i, "LIQ")
            halted = st["liquidated"] = str(idx[i])
        last_c = c[i]
        mark = cash + (qty * (entry - last_c) if qty else 0.0)
        if mark < day_start * (1 - DAY_CAP):
            if qty:
                close(c[i], i, "daycap")
                st["daycap_hits"] += 1
                mark = cash
            blocked = True
        peak = max(peak, mark)
        if halted is None and mark <= peak * (1 - HALT):
            halted = st["halted"] = str(idx[i])
            if qty:
                close(c[i], i, "HALT")
                mark = cash
        if qty and regime_exit and new4[i] and flip[i]:
            pend_exit = True
        if not qty and sig[i] and np.isfinite(atr[i]):
            pend = (float(atr[i]), float(sw[i]))
        if hours[i] == 23:
            marks[idx[i].floor("1D")] = mark
    if qty:
        close(last_c, len(b) - 1, "end")
    eq = pd.Series(marks).sort_index()
    eq.iloc[-1] = cash
    return pd.concat([pd.Series([EQ0], index=[eq.index[0] - pd.Timedelta(days=1)]), eq]), pd.DataFrame(trades), st


def stats(eq, tr, st):
    r = np.log(eq / eq.shift()).dropna()
    ye = eq.groupby(eq.index.year).last()
    ys = np.r_[eq.iloc[0], ye.to_numpy()[:-1]]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    out = {"net": float(eq.iloc[-1] / EQ0 - 1), "cagr": float((eq.iloc[-1] / EQ0) ** (1 / yrs) - 1),
           "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0,
           "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
           "years": {int(y): float(v) for y, v in zip(ye.index, ye.to_numpy() / ys - 1, strict=True)},
           "trades": int(len(tr)), **{k: v for k, v in st.items() if k not in ("fees", "funding")},
           "fees_pct": st["fees"] / EQ0 * 100, "funding_pct": st["funding"] / EQ0 * 100}
    if len(tr):
        out |= {"win_rate": float((tr["pnl_pct"] > 0).mean()), "avg_hours": float(tr["hours"].mean()),
                "exits": tr["why"].value_counts().to_dict(), "avg_sl_dist_pct": float(tr["sl_dist_pct"].mean()),
                "avg_notional_x": float(tr["notional_x"].mean()), "trades_per_year": len(tr) / yrs}
    out["worst_year"] = min(out["years"].values())
    return out


def main():
    np.seterr(all="ignore")
    (OUT / "trades").mkdir(parents=True, exist_ok=True)
    h1, fund = run_r23.market("btc")
    f = features(h1)
    variants = {"T1.5": (1.5, False), "T2": (2.0, False), "T3": (3.0, False), "T2X": (2.0, True)}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rep = {"plan": "docs/research/r24-pullback-short-plan.md @ abbb5af", "commit": commit, "window": [START, END],
           "signal_bars": int(f.loc[START:END, "signal"].sum()), "regime_share": float(f.loc[START:END, "regime"].mean()),
           "runs": {}}
    curves = {}
    for cap in (1.0, 2.0):
        for name, (tp_r, rx) in variants.items():
            key = f"{name}_cap{int(cap)}x"
            res = {}
            for tag, kw in (("base", {}), ("cost2", {"cost_mult": 2.0}), ("zero_cost", {"cost_mult": 0.0}),
                            ("tp_first", {"tp_first": True})):
                eq, tr, st = simulate(h1, fund, f, tp_r, rx, cap, **kw)
                res[tag] = stats(eq, tr, st)
                if tag == "base":
                    curves[key] = eq
                    tr.to_csv(OUT / "trades" / f"{key}.csv", index=False)
            rep["runs"][key] = res
            b = res["base"]
            print(f"{key:12s} net {b['net']:+7.1%} sh {b['sharpe']:5.2f} dd {b['max_dd']:5.1%} n {b['trades']:4d} "
                  f"win {b.get('win_rate', 0):.0%} halt {b['halted']} | x2 {res['cost2']['net']:+7.1%} "
                  f"| 0cost {res['zero_cost']['net']:+7.1%} | tpfirst {res['tp_first']['net']:+7.1%}", flush=True)
    for cap in (1, 2):
        ks = [f"{n}_cap{cap}x" for n in variants]
        pos = sum(rep["runs"][k]["base"]["net"] > 0 for k in ks)
        for k in ks:
            b, c2 = rep["runs"][k]["base"], rep["runs"][k]["cost2"]
            rep["runs"][k]["pass"] = bool(b["net"] > 0 and c2["net"] > 0 and b["max_dd"] <= 0.10 and not b["halted"]
                                          and b["sharpe"] >= 0.5 and b["worst_year"] >= -0.05 and pos >= 3)
    pd.DataFrame(curves).to_csv(OUT / "equity.csv")
    (OUT / "report.json").write_text(json.dumps(jsonable(rep), indent=2))
    print({k: v["pass"] for k, v in rep["runs"].items()}, rep["signal_bars"], rep["regime_share"])


if __name__ == "__main__":
    main()
