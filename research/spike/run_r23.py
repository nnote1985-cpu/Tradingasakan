"""R23: standalone short-only perp strategies, 1x vs 2x, daily vs hourly management.
Pre-declared in docs/research/r23-short-only-plan.md (commits 87020d4, b099924).

    PYTHONPATH=research/spike uv run python research/spike/run_r23.py btc
    PYTHONPATH=research/spike uv run python research/spike/run_r23.py eth1 --rule <id>   # once, after BTC is committed

Data (downloaded beforehand): spot 1h before 2020-01-01, USD-M perp 1h after, perp funding from 2020-01.
BTC C2 (2026), ETH-2 (2022-2025) and ETH 2026 are never loaded (guards below).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable

from trading.market_data.binance_public import load, load_funding

FEE, SLIP, MAINT = 0.0005, 2.0 / 1e4, 0.005
RISK, R1_CAP, DAY_CAP, HALT = 0.01, 2.0, 0.03, 0.10
EQ0 = 10_000.0
SPLICE = pd.Timestamp("2020-01-01", tz="UTC")
ASSETS = {
    "btc": {"sym": "BTCUSDT", "spot": "data/btc_spot_h1", "perp": "data/btc_um_all",
            "window": ("2018-04-01", "2026-01-01")},
    "eth1": {"sym": "ETHUSDT", "spot": "data/eth_spot_h1", "perp": "data/eth_um_all",
             "window": ("2018-04-01", "2022-01-01")},
}
OUT = Path("/mnt/project-files/r23-results")


# ---------------------------------------------------------------- data
def market(asset: str) -> tuple[pd.DataFrame, pd.Series]:
    a = ASSETS[asset]
    spot = load(Path(a["spot"]), a["sym"], "1h")
    perp = load(Path(a["perp"]), a["sym"], "1h")
    h1 = pd.concat([spot[spot.index < SPLICE], perp[perp.index >= SPLICE]])
    h1 = h1[~h1.index.duplicated()].sort_index()
    end = pd.Timestamp(a["window"][1], tz="UTC")
    if h1.index.max() >= end:
        raise RuntimeError("unspent-data guard: bars at/after window end")
    grid = pd.date_range(h1.index[0], h1.index[-1], freq="1h", tz="UTC")
    h1 = h1[["open", "high", "low", "close"]].reindex(grid)
    f = load_funding(Path(a["perp"]), a["sym"])
    f = f[f.index < end]
    fund = f.groupby(f.index.floor("1h")).sum().reindex(grid).fillna(0.0)
    return h1, fund


def daily_bars(h1: pd.DataFrame, fund: pd.Series) -> pd.DataFrame:
    g = h1.groupby(h1.index.floor("1D"))
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    c, h, lo = d["close"].ffill(), d["high"].ffill(), d["low"].ffill()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    fs = fund[fund != 0]
    fday = fs.groupby(fs.index.floor("1D")).agg(["sum", "count"]).reindex(d.index).fillna(0.0)
    s3, n3 = fday["sum"].rolling(3).sum(), fday["count"].rolling(3).sum()
    d["fund3"] = (s3 / n3).where(n3 >= 6)
    d["c"] = c
    return d


# ---------------------------------------------------------------- rules
def variants() -> list[dict]:
    v = []
    for n, k in product((50, 100, 200), (2, 3, 4)):
        v.append({"fam": "S1", "id": f"S1_sma{n}_k{k}", "n": n, "k": k, "tp": None, "maxd": None})
    for nn, mm, k, flt in product((20, 55), (10, 20), (2, 3), (False, True)):
        v.append({"fam": "S2", "id": f"S2_N{nn}_M{mm}_k{k}{'_f200' if flt else ''}", "N": nn, "M": mm, "k": k,
                  "flt": flt, "tp": None, "maxd": None})
    for p, k in product((0.15, 0.25), (1.5, 2.5)):
        v.append({"fam": "S3", "id": f"S3_p{int(p * 100)}_k{k}", "p": p, "k": k, "tp": 2.0, "maxd": 5})
    for f, k in product((0.0003, 0.0005), (1.5, 2.5)):
        v.append({"fam": "S4", "id": f"S4_f{f * 1e4:.0f}bp_k{k}", "f": f, "k": k, "tp": 2.0, "maxd": 5})
    return [dict(x, mode=m, id=f"{x['id']}_{m}") for x in v for m in ("D", "H")]


def signals(d: pd.DataFrame, v: dict) -> tuple[np.ndarray, np.ndarray]:
    c = d["c"]
    if v["fam"] == "S1":
        sma = c.rolling(v["n"], min_periods=v["n"]).mean()
        on, st, turned = np.zeros(len(d), bool), False, np.zeros(len(d), bool)
        lo, hi = (c < sma * 0.98).to_numpy(), (c > sma * 1.02).to_numpy()
        for i in range(len(d)):
            prev = st
            if lo[i]:
                st = True
            elif hi[i]:
                st = False
            on[i], turned[i] = st, st and not prev
        low10 = (c < c.rolling(10).min().shift(1)).to_numpy()
        return on & (turned | low10), ~on
    if v["fam"] == "S2":
        e = c < c.rolling(v["N"]).min().shift(1)
        if v["flt"]:
            e &= c < c.rolling(200, min_periods=200).mean()
        return e.to_numpy(), (c > c.rolling(v["M"]).max().shift(1)).to_numpy()
    if v["fam"] == "S3":
        return (c / c.shift(5) - 1 > v["p"]).to_numpy(), np.zeros(len(d), bool)
    if v["fam"] == "S4":
        ema10 = c.ewm(span=10, adjust=False, min_periods=10).mean()
        return ((d["fund3"] > v["f"]) & (c < ema10)).to_numpy(), np.zeros(len(d), bool)
    raise ValueError(v)


# ---------------------------------------------------------------- simulator
def simulate(h1: pd.DataFrame, fund: pd.Series, d: pd.DataFrame, v: dict, start: str, end: str,
             sizing: str = "R1", halt: bool = True, cost_mult: float = 1.0) -> tuple[pd.Series, pd.DataFrame, dict]:
    fee, slip = FEE * cost_mult, SLIP * cost_mult
    ent_sig, ex_sig = signals(d, v)
    atr = d["atr"].to_numpy()
    sel = (h1.index >= pd.Timestamp(start, tz="UTC")) & (h1.index < pd.Timestamp(end, tz="UTC"))
    bars = h1.loc[sel]
    o, hi, lo, c = (bars[x].to_numpy() for x in ("open", "high", "low", "close"))
    rate = fund.loc[sel].to_numpy()
    hc24 = h1["close"].rolling(24, min_periods=20).max().shift(1).loc[sel].to_numpy()
    hours = bars.index.hour.to_numpy()
    days = bars.index.floor("1D")
    dpos = d.index.get_indexer(days)
    cash, qty, entry, sl, tp, be, atr_e, e_day = EQ0, 0.0, np.nan, np.nan, np.nan, False, np.nan, -1
    peak, halted, day_start, last_c = EQ0, None, EQ0, np.nan
    pend_entry, pend_exit = None, None
    trades, marks = [], {}
    st = {"fees": 0.0, "funding": 0.0, "daycap_hits": 0, "cap_bound": 0, "halted": None, "liquidated": None,
          "hours_in_market": 0}

    def close(raw, i, why):
        nonlocal cash, qty
        px = raw * (1 + slip)
        f = qty * px * fee
        pnl = qty * (entry - px) - f
        cash += pnl
        st["fees"] += f
        tr = trades[-1]
        tr.update({"exit_time": str(bars.index[i]), "exit": px, "why": why,
                   "pnl_pct_equity": (cash - tr["eq_in"]) / tr["eq_in"] * 100,
                   "days": (bars.index[i] - pd.Timestamp(tr["entry_time"])) / pd.Timedelta(days=1)})
        qty = 0.0

    for i in range(len(bars)):
        hr = hours[i]
        if hr == 0:
            day_start = cash + (qty * (entry - last_c) if qty else 0.0)
        if np.isfinite(o[i]):
            if qty and rate[i]:
                r = qty * o[i] * rate[i]
                cash += r
                st["funding"] += r
            if pend_exit and qty:
                close(o[i], i, pend_exit)
            pend_exit = None
            if pend_entry is not None and not qty and halted is None:
                a_ = pend_entry
                px = o[i] * (1 - slip)
                sl_d = v["k"] * a_
                if sizing == "R1":
                    nf = RISK / (sl_d / px)
                    if nf > R1_CAP:
                        nf, st["cap_bound"] = R1_CAP, st["cap_bound"] + 1
                else:
                    nf = {"N1": 1.0, "N2": 2.0}[sizing]
                eq_in = cash
                qty = nf * cash / px
                f = qty * px * fee
                cash -= f
                st["fees"] += f
                entry, sl, be, atr_e, e_day = px, px + sl_d, False, a_, dpos[i]
                tp = px - v["tp"] * a_ if v["tp"] else -np.inf
                trades.append({"entry_time": str(bars.index[i]), "entry": px, "sl": sl, "notional_x": nf,
                               "sl_dist_pct": sl_d / px * 100, "eq_in": eq_in})
            pend_entry = None
            if qty:
                st["hours_in_market"] += 1
                if hi[i] >= sl:
                    close(max(o[i], sl), i, "SL_BE" if be else "SL")
                elif lo[i] <= tp:
                    close(min(o[i], tp), i, "TP")
            if qty:  # liquidation on the 1h high, cross margin on the whole account
                eq_hi = cash + qty * (entry - hi[i])
                if eq_hi <= MAINT * qty * hi[i]:
                    close(hi[i], i, "LIQ")
                    cash = max(cash, 0.0)
                    halted = st["liquidated"] = str(bars.index[i])
            if qty and v["mode"] == "H":
                if not be and lo[i] <= entry - atr_e:
                    be, sl = True, min(sl, entry)
                if np.isfinite(hc24[i]) and c[i] > hc24[i]:
                    close(c[i], i, "hourly")
            last_c = c[i] if np.isfinite(c[i]) else last_c
            mark = cash + (qty * (entry - last_c) if qty else 0.0)
            if mark < day_start * (1 - DAY_CAP) and qty:
                close(c[i], i, "daycap")
                st["daycap_hits"] += 1
                mark = cash
            peak = max(peak, mark)
            if halt and halted is None and mark <= peak * (1 - HALT):
                halted = st["halted"] = str(bars.index[i])
                if qty:
                    close(c[i], i, "HALT")
                    mark = cash
        if hr == 23:
            di = dpos[i]
            marks[days[i]] = cash + (qty * (entry - last_c) if qty else 0.0)
            if di < 0:
                continue
            if qty:
                if ex_sig[di]:
                    pend_exit = "signal"
                elif v["maxd"] and di - e_day + 1 >= v["maxd"]:
                    pend_exit = "time"
            elif halted is None and ent_sig[di] and np.isfinite(atr[di]) and day_start * (1 - DAY_CAP) <= cash:
                pend_entry = float(atr[di])
    if qty:
        close(last_c, len(bars) - 1, "end")
        marks[days[-1]] = cash
    eq = pd.Series(marks).sort_index()
    eq = pd.concat([pd.Series([EQ0], index=[eq.index[0] - pd.Timedelta(days=1)]), eq])
    return eq, pd.DataFrame(trades), st


def stats(eq: pd.Series, trades: pd.DataFrame, st: dict, n_hours: int) -> dict:
    r = np.log(eq / eq.shift()).dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ye = eq.groupby(eq.index.year).last()
    ys = np.r_[eq.iloc[0], ye.to_numpy()[:-1]]
    out = {"net": float(eq.iloc[-1] / EQ0 - 1), "cagr": float((eq.iloc[-1] / EQ0) ** (1 / yrs) - 1),
           "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0,
           "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
           "years": {int(y): float(x) for y, x in zip(ye.index, ye.to_numpy() / ys - 1, strict=True)},
           "trades": int(len(trades)), "time_in_market": st["hours_in_market"] / max(n_hours, 1),
           "fees_pct": st["fees"] / EQ0 * 100, "funding_pct": st["funding"] / EQ0 * 100,
           "daycap_hits": st["daycap_hits"], "cap_bound": st["cap_bound"], "halted": st["halted"],
           "liquidated": st["liquidated"]}
    if len(trades):
        out |= {"win_rate": float((trades["pnl_pct_equity"] > 0).mean()), "avg_days": float(trades["days"].mean()),
                "exits": trades["why"].value_counts().to_dict(), "avg_notional_x": float(trades["notional_x"].mean()),
                "worst_trade_pct": float(trades["pnl_pct_equity"].min()), "best_trade_pct": float(trades["pnl_pct_equity"].max())}
    out["worst_year"] = min(out["years"].values())
    return out


def run_all(h1, fund, d, vs, start, end, out: Path) -> tuple[list[dict], dict]:
    n_hours = int(((h1.index >= pd.Timestamp(start, tz="UTC")) & (h1.index < pd.Timestamp(end, tz="UTC"))).sum())
    rows, curves = [], {}
    for v in vs:
        row = {"id": v["id"], "fam": v["fam"], "mode": v["mode"]}
        for tag, kw in (("R1", {}), ("R1_cost2", {"cost_mult": 2.0}), ("N1", {"sizing": "N1"}), ("N2", {"sizing": "N2"}),
                        ("N1_nohalt", {"sizing": "N1", "halt": False}), ("N2_nohalt", {"sizing": "N2", "halt": False})):
            eq, tr, st = simulate(h1, fund, d, v, start, end, **kw)
            row[tag] = stats(eq, tr, st, n_hours)
            curves[f"{v['id']}|{tag}"] = eq
            if tag == "R1":
                tr.to_csv(out / "trades" / f"{v['id']}_R1.csv", index=False)
        rows.append(row)
        print(f"{v['id']:28s} R1 net {row['R1']['net']:+7.1%} sh {row['R1']['sharpe']:5.2f} dd {row['R1']['max_dd']:5.1%} "
              f"n {row['R1']['trades']:3d} | N1 {row['N1_nohalt']['net']:+7.1%} dd {row['N1_nohalt']['max_dd']:5.1%} "
              f"| N2 {row['N2_nohalt']['net']:+8.1%} dd {row['N2_nohalt']['max_dd']:5.1%}", flush=True)
    return rows, curves


def select(rows: list[dict]) -> dict:
    fam_pos: dict = {}
    for r in rows:
        fam_pos.setdefault((r["fam"], r["mode"]), []).append(r["R1"]["net"] > 0)
    res = {}
    for r in rows:
        a, b = r["R1"], r["R1_cost2"]
        fp = fam_pos[(r["fam"], r["mode"])]
        crit = {"1_net_pos_and_cost2": a["net"] > 0 and b["net"] > 0,
                "2_dd_le_10_no_halt": a["max_dd"] <= 0.10 and a["halted"] is None and a["liquidated"] is None,
                "3_sharpe_ge_0.5": a["sharpe"] >= 0.5, "4_worst_year_ge_-5": a["worst_year"] >= -0.05,
                "5_family_half_positive": sum(fp) >= len(fp) / 2}
        res[r["id"]] = {"pass": all(crit.values()), **crit}
    passers = [r for r in rows if res[r["id"]]["pass"]]
    chosen = max(passers, key=lambda r: r["R1"]["sharpe"])["id"] if passers else None
    return {"criteria": res, "passers": [r["id"] for r in passers], "chosen": chosen}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("asset", choices=list(ASSETS))
    ap.add_argument("--rule")
    a = ap.parse_args()
    np.seterr(all="ignore")
    out = OUT / a.asset
    if a.asset == "eth1":
        btc = json.loads((OUT / "btc" / "report.json").read_text())
        if not btc["selection"]["chosen"] or a.rule != btc["selection"]["chosen"]:
            raise SystemExit("ETH-1 runs only the chosen BTC rule")
        if (out / "report.json").exists():
            raise SystemExit("ETH-1 already run for R23")
    (out / "trades").mkdir(parents=True, exist_ok=True)
    start, end = ASSETS[a.asset]["window"]
    h1, fund = market(a.asset)
    d = daily_bars(h1, fund)
    vs = [v for v in variants() if a.rule in (None, v["id"])]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rows, curves = run_all(h1, fund, d, vs, start, end, out)
    rep = {"plan": "docs/research/r23-short-only-plan.md", "commit": commit, "window": [start, end], "rows": rows}
    if a.asset == "btc":
        rep["selection"] = select(rows)
        print(json.dumps({k: rep["selection"][k] for k in ("passers", "chosen")}, indent=1))
    keep = {k: v for k, v in curves.items() if k.endswith(("|R1", "|N1_nohalt", "|N2_nohalt"))}
    pd.DataFrame(keep).to_csv(out / "equity.csv")
    flat = [{"id": r["id"], **{f"{t}_{m}": r[t][m] for t in ("R1", "R1_cost2", "N1", "N2", "N1_nohalt", "N2_nohalt")
                               for m in ("net", "sharpe", "max_dd", "worst_year", "trades")}} for r in rows]
    pd.DataFrame(flat).to_csv(out / "summary.csv", index=False)
    (out / "report.json").write_text(json.dumps(jsonable(rep), indent=2))


if __name__ == "__main__":
    main()
