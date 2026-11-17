"""R16: three hourly-signal futures systems, pre-declared in docs/research/r16-hourly-signal-systems-plan.md
(commit a66fd3f). Six runs per window; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r16.py dev
    PYTHONPATH=research/spike uv run python research/spike/run_r16.py c1     # once, after dev is committed

Uses the perp files R15 downloaded (data/binance_um, data/binance_um_c1); no 2026 data.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.trend_breakout_v1 import regularize_h1

EQUITY = 10_000.0
FEE, SLIP = 0.0005, 2.0 / 1e4
WINDOWS = {"dev": ("2022-05-01", "2026-01-01", "data/binance_um"),
           "c1": ("2020-04-15", "2022-01-01", "data/binance_um_c1")}
R15 = "/mnt/project-files/r15-results"
STOP_ATR = 2.0


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn)


def atr(h, lo, c, n=14):
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def adx(h, lo, c, n=14):
    up, dn = h.diff(), -lo.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    a = atr(h, lo, c, n)
    pdi, mdi = 100 * wilder(pdm, n) / a, 100 * wilder(mdm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return wilder(dx, n)


def features(h1: pd.DataFrame) -> pd.DataFrame:
    f = h1.ffill()          # indicators bridge the few missing hours; no signal/fill on them (see loop)
    c, h, lo = f["close"], f["high"], f["low"]
    out = pd.DataFrame(index=h1.index)
    out["rsi"] = rsi(c)
    out["atr"] = atr(h, lo, c)
    out["hh48"], out["ll48"] = h.rolling(48).max().shift(1), lo.rolling(48).min().shift(1)
    out["hh24"], out["ll24"] = h.rolling(24).max().shift(1), lo.rolling(24).min().shift(1)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    out["bb_mid"], out["bb_lo"], out["bb_hi"] = mid, mid - 2 * sd, mid + 2 * sd
    h4 = f.resample("4h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    h4f = pd.DataFrame({"trend": np.sign(h4["close"] - h4["close"].ewm(span=50, adjust=False, min_periods=50).mean()),
                        "adx": adx(h4["high"], h4["low"], h4["close"])})
    h4f.index = h4f.index + pd.Timedelta(hours=3)   # known at the close of the H4 bar's last hour
    out = out.join(h4f.reindex(out.index).ffill())
    return out


def simulate(h1: pd.DataFrame, ft: pd.DataFrame, funding: pd.Series, system: str, intraday: bool,
             start, end, fee=FEE, slip=SLIP):
    bars = h1.loc[(h1.index >= pd.Timestamp(start, tz="UTC")) & (h1.index < pd.Timestamp(end, tz="UTC"))]
    F = ft.reindex(bars.index)
    cash, side, qty, entry, stop, mode, t_in = EQUITY, 0, 0.0, np.nan, np.nan, "", None
    regime = "range"
    pending: tuple | None = None       # (new_side, mode, stop_dist) to execute at next open; new_side 0 = exit
    trades, hourly = [], []
    st = {"fees": 0.0, "funding_paid": 0.0, "funding_events": 0, "stops": 0}
    fund_at = funding

    def close_pos(px_raw: float, t, why: str):
        nonlocal cash, side, qty
        px = px_raw * (1 - side * slip)
        pnl = side * qty * (px - entry)
        f = qty * px * fee
        cash += pnl - f
        st["fees"] += f
        trades.append({"entry_time": t_in, "exit_time": t, "side": side, "entry": entry, "exit": px, "mode": mode,
                       "pnl": pnl - f - entry_fee, "why": why, "hours": (t - t_in) / pd.Timedelta(hours=1)})
        side, qty = 0, 0.0

    def open_pos(o: float, t, new_side: int, m: str, stop_dist: float):
        nonlocal cash, side, qty, entry, stop, mode, t_in, entry_fee
        px = o * (1 + new_side * slip)
        qty = cash / px
        entry_fee = qty * px * fee
        cash -= entry_fee
        st["fees"] += entry_fee
        side, entry, mode, t_in = new_side, px, m, t
        stop = px - new_side * stop_dist

    entry_fee = 0.0
    last_c = np.nan
    for t, row in bars.iterrows():
        o, hi, lo, c = row["open"], row["high"], row["low"], row["close"]
        if not np.isfinite(o):
            hourly.append(cash + (side * qty * (last_c - entry) if side else 0.0))
            continue
        # funding settlement at this bar's start, on the position held into it
        if side and t in fund_at.index:
            pay = side * qty * o * float(fund_at.loc[t])
            cash -= pay
            st["funding_paid"] += pay
            st["funding_events"] += 1
        if pending is not None:
            ns, nm, sd = pending
            pending = None
            if side and ns != side:
                close_pos(o, t, "signal")
            if ns and not side:
                open_pos(o, t, ns, nm, sd)
        if side:
            hit = (lo <= stop) if side > 0 else (hi >= stop)
            if hit:
                px = min(o, stop) if side > 0 else max(o, stop)
                close_pos(px, t, "stop")
                st["stops"] += 1
        last_c = c
        hour = t.hour
        if intraday and hour == 23 and side:
            close_pos(c, t + pd.Timedelta(hours=1), "eod")
        # signals at this close
        x = F.loc[t]
        if np.isfinite(c) and np.all(np.isfinite([x["atr"]])):
            want, wmode = None, ""
            # exits for the open position
            if side:
                if mode == "pullback":
                    held = (t + pd.Timedelta(hours=1) - t_in) / pd.Timedelta(hours=1)
                    if (side > 0 and (x["rsi"] > 55 or x["trend"] < 0)) or (side < 0 and (x["rsi"] < 45 or x["trend"] > 0)) or held >= 48:
                        want = 0
                elif mode == "breakout":
                    if (side > 0 and c < x["ll24"]) or (side < 0 and c > x["hh24"]):
                        want = 0
                elif mode == "meanrev":
                    if (side > 0 and c >= x["bb_mid"]) or (side < 0 and c <= x["bb_mid"]):
                        want = 0
            # entries
            if system == "C" and np.isfinite(x["adx"]):
                regime = "trend" if x["adx"] > 25 else "range" if x["adx"] < 20 else regime
            sig, smode = 0, ""
            if system == "A" and np.isfinite(x["trend"]) and np.isfinite(x["rsi"]):
                sig = 1 if (x["trend"] > 0 and x["rsi"] < 30) else -1 if (x["trend"] < 0 and x["rsi"] > 70) else 0
                smode = "pullback"
            elif system == "B" or (system == "C" and regime == "trend"):
                sig = 1 if c > x["hh48"] else -1 if c < x["ll48"] else 0
                smode = "breakout"
            elif system == "C":
                sig = 1 if c < x["bb_lo"] else -1 if c > x["bb_hi"] else 0
                smode = "meanrev"
            no_entry = intraday and hour >= 22
            if sig and sig != side and not no_entry:
                want, wmode = sig, smode
            if intraday and hour == 23:
                want = None
            if want is not None and (want != side):
                pending = (want, wmode, STOP_ATR * float(x["atr"]))
        hourly.append(cash + (side * qty * (c - entry) if side else 0.0))
    if side:
        close_pos(last_c, bars.index[-1] + pd.Timedelta(hours=1), "end")
        hourly[-1] = cash
    hq = pd.Series(hourly, index=bars.index + pd.Timedelta(hours=1))
    return hq, pd.DataFrame(trades), st


def daily_stats(hq: pd.Series, trades: pd.DataFrame, start, end) -> dict:
    d_eq = hq.resample("1D", label="left", closed="right").last()   # equity at each UTC day's end (00:00 mark)
    d_eq.index = d_eq.index.tz_localize(None)
    d_eq = d_eq[(d_eq.index >= pd.Timestamp(start)) & (d_eq.index < pd.Timestamp(end))]
    eq = pd.concat([pd.Series([EQUITY], index=[d_eq.index[0] - pd.Timedelta(days=1)]), d_eq]).ffill()
    r = eq.pct_change().dropna()
    pnl = eq.diff().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365
    net = float(eq.iloc[-1] / EQUITY - 1)
    months = yrs * 12
    has_pos = pd.Series(False, index=r.index)
    if len(trades):
        for _, tr in trades.iterrows():
            a = pd.Timestamp(tr["entry_time"]).tz_localize(None).floor("1D")
            b = (pd.Timestamp(tr["exit_time"]).tz_localize(None) - pd.Timedelta(seconds=1)).floor("1D")
            has_pos[(has_pos.index >= a) & (has_pos.index <= b)] = True
    out = {"net": net, "cagr": float((1 + net) ** (1 / yrs) - 1) if net > -1 else -1.0,
           "sharpe": sharpe(daily_log_returns(eq)) if (eq > 0).all() else float("nan"),
           "max_dd": max_drawdown(eq), "max_dd_hourly": max_drawdown(hq),
           "daily": {"mean_pct": float(r.mean() * 100), "median_pct": float(r.median() * 100),
                     "std_pct": float(r.std() * 100), "up_days_pct": float((r > 1e-12).mean() * 100),
                     "down_days_pct": float((r < -1e-12).mean() * 100),
                     "no_position_days_pct": float((~has_pos).mean() * 100),
                     "avg_pnl_usdt": float(pnl.mean()), "best_day_pct": float(r.max() * 100),
                     "worst_day_pct": float(r.min() * 100)},
           "trades": int(len(trades)), "trades_per_month": float(len(trades) / months)}
    if len(trades):
        out["win_rate"] = float((trades["pnl"] > 0).mean())
        out["avg_hold_h"] = float(trades["hours"].mean())
        out["exits"] = trades["why"].value_counts().to_dict()
        out["long_short"] = trades["side"].value_counts().to_dict()
    return out | {"_eq": eq}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("window", choices=list(WINDOWS))
    ap.add_argument("--out", default="/mnt/project-files/r16-results")
    a = ap.parse_args()
    start, end, ddir = WINDOWS[a.window]
    out = Path(a.out) / a.window
    if a.window == "c1":
        if not (Path(a.out) / "dev" / "report.json").exists():
            raise SystemExit("run dev first")
        if (out / "report.json").exists():
            raise SystemExit("C1 already run for R16")
    out.mkdir(parents=True, exist_ok=True)
    np.seterr(all="ignore")
    raw = load(Path(ddir), "BTCUSDT", "1h")
    if raw.index.max() >= pd.Timestamp("2026-01-01", tz="UTC"):
        raise RuntimeError("2026 data present: C2 guard")
    h1 = regularize_h1(raw)
    f = load_funding(Path(ddir), "BTCUSDT")
    funding = f.groupby(f.index.floor("1h")).sum()
    ft = features(h1)
    r15 = json.loads((Path(R15) / a.window / "report.json").read_text())["benchmarks"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    keys = ("net", "sharpe", "max_dd")
    bench = {k: {m: r15[k][m] for m in keys} | {"costs_x2": {m: r15[k]["costs_x2"][m] for m in keys}}
             for k in ("R10a", "buy_and_hold")}
    report: dict = {"plan": "docs/research/r16-hourly-signal-systems-plan.md @ a66fd3f", "commit": commit,
                    "window": [start, end], "benchmarks_from_R15": bench, "runs": {}, "verdict": {}}
    curves = {}
    for system in ("A", "B", "C"):
        for intraday in (False, True):
            name = f"{system}-{'I' if intraday else 'O'}"
            hq, tr, st = simulate(h1, ft, funding, system, intraday, start, end)
            ds = daily_stats(hq, tr, start, end)
            curves[name] = ds.pop("_eq")
            hq2, tr2, _ = simulate(h1, ft, funding, system, intraday, start, end, FEE * 2, SLIP * 2)
            ds2 = daily_stats(hq2, tr2, start, end)
            ds2.pop("_eq")
            report["runs"][name] = ds | st | {"costs_x2": {k: ds2[k] for k in ("net", "sharpe", "max_dd")}}
            tr.to_csv(out / f"trades_{name}.csv", index=False)
    pd.DataFrame(curves).to_csv(out / "daily_equity.csv")
    R, B = report["benchmarks_from_R15"]["R10a"], report["benchmarks_from_R15"]["buy_and_hold"]

    def beats_bh(m, b):
        return bool(m["net"] > 0 and (m["sharpe"] > b["sharpe"] or m["max_dd"] <= 0.5 * b["max_dd"]))

    for name, m in report["runs"].items():
        A_ = bool(m["sharpe"] > R["sharpe"] and m["max_dd"] <= R["max_dd"] + 0.05)
        report["verdict"][name] = {"A_beats_R10a": A_, "B_beats_BH": beats_bh(m, B),
                                   "C_costs_x2": beats_bh(m["costs_x2"], B["costs_x2"]), "no_ruin": bool(m["net"] > -1)}
        report["verdict"][name]["pass"] = all(report["verdict"][name].values())
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(f"{a.window}: R10a {R['net']:+.1%}/{R['sharpe']:.2f}/{R['max_dd']:.1%}  B&H {B['net']:+.1%}/{B['sharpe']:.2f}/{B['max_dd']:.1%}")
    for name, m in report["runs"].items():
        d = m["daily"]
        print(f"  {name}: net {m['net']:+.1%} sh {m['sharpe']:.2f} dd {m['max_dd']:.1%} | trades {m['trades']} ({m['trades_per_month']:.1f}/mo) "
              f"win {m.get('win_rate', 0):.0%} hold {m.get('avg_hold_h', 0):.0f}h | day mean {d['mean_pct']:+.3f}% med {d['median_pct']:+.3f}% "
              f"up {d['up_days_pct']:.0f}% dn {d['down_days_pct']:.0f}% flat {d['no_position_days_pct']:.0f}% avg {d['avg_pnl_usdt']:+.1f} USDT "
              f"| fees {m['fees']:.0f} fund {m['funding_paid']:+.0f} ({m['funding_events']}) | x2 {m['costs_x2']['net']:+.1%} "
              f"{'PASS' if report['verdict'][name]['pass'] else 'fail'}")


if __name__ == "__main__":
    main()
