"""R15: futures strategy search on BTCUSDT perp, pre-declared in docs/research/r15-futures-search-plan.md
(commit 41f419a). Eight runs per window; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r15.py dev
    PYTHONPATH=research/spike uv run python research/spike/run_r15.py c1    # once, after dev is committed

No 2026 file is ever downloaded here: C2 stays unused.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable
from run_r10 import BAND, latch

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import _download_zip, _months, download, load, load_funding, parse_klines_csv
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

EQUITY = 10_000.0
SPOT_BASE = "https://data.binance.vision/data/spot"
WINDOWS = {
    "dev": {"range": ("2022-05-01", "2026-01-01"), "data": (date(2022, 1, 1), date(2025, 12, 31)),
            "perp_dir": "data/binance_um", "spot_dir": "data/binance_spot",
            "years": [("2022 (May-Dec)", "2022-05-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
                      ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]},
    "c1": {"range": ("2020-04-15", "2022-01-01"), "data": (date(2020, 1, 1), date(2021, 12, 31)),
           "perp_dir": "data/binance_um_c1", "spot_dir": "data/binance_spot_c1",
           "years": [("2020 (Apr-Dec)", "2020-04-15", "2021-01-01"), ("2021", "2021-01-01", "2022-01-01")]},
}
MAINT = 0.005
CARRY_ON, CARRY_OFF = 0.00015, 0.0      # 7-day mean funding per day: 0.005%/8h on, < 0 off
SLOPE_DAYS = 20
TSMOM_LOOKBACKS, TARGET_VOL = (30, 60, 90), 0.40
REB_REL, REB_MIN = 0.20, 0.05


@dataclass(frozen=True)
class Costs:
    perp_fee: float = 0.0005
    spot_fee: float = 0.0010
    slip: float = 2.0 / 1e4

    def x(self, m: float) -> Costs:
        return Costs(self.perp_fee * m, self.spot_fee * m, self.slip * m)


def _naive(ix: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return ix.tz_localize(None) if ix.tz is not None else ix


def market(win: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, dict]:
    a, b = win["data"]
    perp_dir, spot_dir = Path(win["perp_dir"]), Path(win["spot_dir"])
    man = download("BTCUSDT", a, b, perp_dir, intervals=("1h",))
    spot_dir.mkdir(parents=True, exist_ok=True)
    (spot_dir / "raw").mkdir(exist_ok=True)
    frames, sfiles = [], []
    for y, m in _months(a, b):
        url = f"{SPOT_BASE}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{y}-{m:02d}.zip"
        inner, info = _download_zip(url, spot_dir / "raw")
        sfiles.append(info)
        if inner is not None:
            frames.append(parse_klines_csv(inner))
    files = man["files"] + sfiles
    bad = [f for f in files if f["status"] != "VERIFIED"]
    if bad:
        raise RuntimeError(f"unverified files: {bad[:3]}")
    h1_raw = load(perp_dir, "BTCUSDT", "1h")
    d = daily_bars(h1_raw)
    h1 = regularize_h1(h1_raw)
    g = h1.groupby(h1.index.floor("1D"))
    d["high"], d["low"] = g["high"].max(), g["low"].min()
    d.loc[~d["valid"], ["high", "low"]] = np.nan
    d.index = _naive(d.index)
    spot = pd.concat(frames).sort_index()
    spot = spot[~spot.index.duplicated()]
    spot.index = _naive(spot.index.floor("1D"))
    f = load_funding(perp_dir, "BTCUSDT")
    f.index = _naive(f.index)
    fd = f.groupby(f.index.floor("1D")).sum()
    for t in ("1h", "funding"):
        if f"coverage_{t}" in man and str(b.year + 1) in man[f"coverage_{t}"][1][:4]:
            raise RuntimeError("data reaches past the window: refusing (C2 guard)")
    if spot.index.max() >= pd.Timestamp(b) + pd.Timedelta(days=1):
        raise RuntimeError("spot data reaches past the window (C2 guard)")
    return d, spot.reindex(d.index), fd, {"files": len(files), "verified": len(files) - len(bad),
                                           "perp_cov": man.get("coverage_1h"), "funding_cov": man.get("coverage_funding"),
                                           "spot_cov": [str(spot.index[0]), str(spot.index[-1]), len(spot)]}


def targets(daily: pd.DataFrame, fd: pd.Series) -> tuple[dict[str, tuple[pd.Series, pd.Series]], pd.DataFrame]:
    """Per variant: (spot weight, perp weight) decided at each day's close."""
    sig = signals(daily, fd)
    c, sma = sig["close"], sig["sma100"]
    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND))
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    r10a = (band * fund)
    falling = sma < sma.shift(SLOPE_DAYS)
    zero = pd.Series(0.0, index=sig.index)

    f7 = sig["funding_7d_per_day"]
    state, carry = 0.0, np.full(len(f7), np.nan)
    for i, v in enumerate(f7.to_numpy()):
        if np.isfinite(v):
            state = 1.0 if v > CARRY_ON else 0.0 if v < CARRY_OFF else state
            carry[i] = state
    carry = pd.Series(carry, index=sig.index)

    cf = c.ffill()
    s = sum(np.sign(cf / cf.shift(k) - 1) for k in TSMOM_LOOKBACKS) / len(TSMOM_LOOKBACKS)
    vol = np.log(cf / cf.shift(1)).rolling(30, min_periods=30).std() * np.sqrt(365)
    scale = (TARGET_VOL / vol).clip(upper=1.0)

    out: dict[str, tuple[pd.Series, pd.Series]] = {}
    for lev in (1, 2, 3):
        long_w = band * fund * lev
        short_w = pd.Series(np.where((band == 0) & falling, -float(lev), 0.0), index=sig.index)
        f1 = long_w.where(band == 1, short_w)
        f1[band.isna()] = np.nan
        out[f"F1_gated_ls_{lev}x"] = (zero, f1)
    out["F2_carry"] = (carry * 0.5, carry * -0.5)
    idle = 1 - r10a
    out["F3_r10a_plus_carry"] = (r10a + 0.5 * idle * carry, -0.5 * idle * carry)
    for lev in (1, 2, 3):
        out[f"F4_tsmom_{lev}x"] = (zero, s * scale * lev)
    out["R10a_spot_sim"] = (r10a, zero)
    feats = pd.DataFrame({"close": c, "sma100": sma, "band": band, "funding_hot": sig["funding_hot"],
                          "sma_falling": falling, "funding_7d_per_day": f7, "carry_on": carry,
                          "tsmom_s": s, "vol30": vol, "r10a_w": r10a})
    return out, feats


def simulate(daily, spot, fd, ws, wp, start, end, costs: Costs):
    days = daily.index[(daily.index >= pd.Timestamp(start)) & (daily.index < pd.Timestamp(end))]
    tws = ws.shift(1).ffill().reindex(days).fillna(0.0)
    twp = wp.shift(1).ffill().reindex(days).fillna(0.0)
    cash, sq, pq, mark = EQUITY, 0.0, 0.0, np.nan
    last_spot = np.nan
    prev_t = (np.nan, np.nan)
    marks, worst_marks, trades = [], [], []
    st = {"fees": 0.0, "slippage": 0.0, "funding_paid": 0.0, "days_long": 0, "days_short": 0, "days_flat": 0,
          "days_carry_or_spot": 0, "liquidated": None, "perp_turnover": 0.0, "spot_turnover": 0.0}
    for d in days:
        o, hi, lo, c = (daily.at[d, k] for k in ("open", "high", "low", "close"))
        so, sh, sl, sc = (spot.at[d, k] for k in ("open", "high", "low", "close"))
        t_s, t_p = float(tws.at[d]), float(twp.at[d])
        if np.isfinite(o) and np.isfinite(so):
            if pq and np.isfinite(mark):
                cash += pq * (o - mark)
            mark = o
            eq = cash + sq * so
            cur_s, cur_p = sq * so / eq, pq * o / eq
            for leg, cur, tgt, prv in (("perp", cur_p, t_p, prev_t[1]), ("spot", cur_s, t_s, prev_t[0])):
                if tgt != prv or abs(cur - tgt) > max(REB_REL * abs(tgt), REB_MIN):
                    if abs(cur - tgt) < 1e-12:
                        continue
                    if leg == "perp":
                        dq = tgt * eq / o - pq
                        px = o * (1 + np.sign(dq) * costs.slip)
                        fee = abs(dq) * px * costs.perp_fee
                        cash -= dq * (px - o) + fee
                        st["slippage"] += dq * (px - o)
                        pq += dq
                        st["perp_turnover"] += abs(dq) * px
                    else:
                        dq = tgt * eq / so - sq
                        px = so * (1 + np.sign(dq) * costs.slip)
                        fee = abs(dq) * px * costs.spot_fee
                        cash -= dq * px + fee
                        st["slippage"] += dq * (px - so)
                        sq += dq
                        st["spot_turnover"] += abs(dq) * px
                    st["fees"] += fee
                    trades.append({"time": d, "leg": leg, "qty": dq, "price": px, "fee": fee, "target": tgt})
            prev_t = (t_s, t_p)
        if np.isfinite(sc):
            last_spot = sc
        # liquidation: perp margin = cash (spot is not margin); worst 1h price of the day
        if pq and np.isfinite(mark):
            w = hi if pq < 0 else lo
            if np.isfinite(w) and cash + pq * (w - mark) <= MAINT * abs(pq) * w:
                cash += pq * (w - mark)
                st["liquidated"] = str(d.date())
                pq = 0.0
                eqv = cash + sq * (sl if np.isfinite(sl) else last_spot)
                marks.append(eqv)
                worst_marks.append(eqv)
                break
        if np.isfinite(c) and np.isfinite(mark) and pq:
            cash += pq * (c - mark)
        if np.isfinite(c):
            mark = c
            if pq and d in fd.index:
                pay = pq * c * float(fd.at[d])
                cash -= pay
                st["funding_paid"] += pay
        eqc = cash + sq * last_spot
        # worst intraday mark: both legs at the day's low, or both at the high (perp settled to close already)
        scen = []
        for pp, sp in ((lo, sl), (hi, sh)):
            if np.isfinite(pp) and np.isfinite(sp):
                scen.append(cash + pq * (pp - c) + sq * sp)
        worst_marks.append(min(scen + [eqc]))
        marks.append(eqc)
        if pq > 0:
            st["days_long"] += 1
        elif pq < 0 and sq == 0:
            st["days_short"] += 1
        elif sq > 0:
            st["days_carry_or_spot"] += 1
        else:
            st["days_flat"] += 1
    idx = days[: len(marks)]
    first = pd.Series([EQUITY], index=[idx[0] - pd.Timedelta(days=1)])
    eq = pd.concat([first, pd.Series(marks, index=idx)])
    weq = pd.concat([first, pd.Series(worst_marks, index=idx)])
    return eq, weq, pd.DataFrame(trades), st


def buy_and_hold(spot: pd.DataFrame, start, end, costs: Costs) -> pd.Series:
    days = spot.loc[(spot.index >= pd.Timestamp(start)) & (spot.index < pd.Timestamp(end))]
    qty = EQUITY * (1 - costs.spot_fee) / (days["open"].dropna().iloc[0] * (1 + costs.slip))
    eq = (qty * days["close"]).ffill()
    return pd.concat([pd.Series([EQUITY], index=[days.index[0] - pd.Timedelta(days=1)]), eq])


def metrics(eq: pd.Series, weq: pd.Series | None = None) -> dict:
    ruined = bool((eq <= 0).any())
    yrs = (eq.index[-1] - eq.index[0]).days / 365.0
    net = float(eq.iloc[-1] / eq.iloc[0] - 1)
    out = {"net": net, "cagr": float((1 + net) ** (1 / yrs) - 1) if net > -1 else -1.0,
           "sharpe": float("nan") if ruined else sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}
    if weq is not None:
        peak = eq.cummax()
        out["max_dd_intraday"] = float(((peak - weq.reindex(eq.index).fillna(eq)) / peak).max())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("window", choices=list(WINDOWS))
    ap.add_argument("--out", default="/mnt/project-files/r15-results")
    a = ap.parse_args()
    win = WINDOWS[a.window]
    out = Path(a.out) / a.window
    if a.window == "c1":
        dev = Path(a.out) / "dev" / "report.json"
        if not dev.exists():
            raise SystemExit("run dev first")
        if (out / "report.json").exists():
            raise SystemExit("C1 already run for R15; see report.json")
    out.mkdir(parents=True, exist_ok=True)
    np.seterr(all="ignore")
    daily, spot, fd, data_info = market(win)
    tg, feats = targets(daily, fd)
    start, end = win["range"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report: dict = {"plan": "docs/research/r15-futures-search-plan.md @ 41f419a", "commit": commit,
                    "window": [start, end], "data": data_info, "benchmarks": {}, "runs": {}}

    # R10a reproduction exactly as run_r10 (perp prices as proxy, spot costs)
    sig_w = feats["r10a_w"]
    r10a_eq, _ = simulate_weights(daily, sig_w, pd.Timestamp(start), pd.Timestamp(end), equity=EQUITY, costs=RegimeCosts())
    r10a_x2, _ = simulate_weights(daily, sig_w, pd.Timestamp(start), pd.Timestamp(end), equity=EQUITY,
                                  costs=RegimeCosts(fee=0.002, slip_bps=4.0))
    report["benchmarks"]["R10a"] = metrics(r10a_eq) | {"costs_x2": metrics(r10a_x2)}
    bh = buy_and_hold(spot, start, end, Costs())
    report["benchmarks"]["buy_and_hold"] = metrics(bh) | {"costs_x2": metrics(buy_and_hold(spot, start, end, Costs().x(2)))}
    r10_ret = np.log(r10a_eq / r10a_eq.shift(1)).dropna()

    curves = {"R10a": r10a_eq, "buy_and_hold": bh}
    for name, (ws, wp) in tg.items():
        res = {}
        for cname, cst in (("base", Costs()), ("costs_x2", Costs().x(2))):
            eq, weq, trades, st = simulate(daily, spot, fd, ws, wp, start, end, cst)
            m = metrics(eq, weq) | st | {"trades": int(len(trades))}
            if cname == "base":
                m["years"] = {y: metrics(eq[(eq.index >= pd.Timestamp(s) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(e))])
                              for y, s, e in win["years"] if len(eq[(eq.index >= pd.Timestamp(s))]) > 1}
                if not (eq <= 0).any():
                    m["corr_with_R10a"] = float(np.log(eq / eq.shift(1)).dropna().corr(r10_ret))
                curves[name] = eq
                trades.to_csv(out / f"trades_{name}.csv", index=False)
                res.update(m)
            else:
                res["costs_x2"] = {k: m[k] for k in ("net", "cagr", "sharpe", "max_dd", "liquidated")}
        report["runs"][name] = res
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    feats.to_csv(out / "daily_features.csv")

    R, B = report["benchmarks"]["R10a"], report["benchmarks"]["buy_and_hold"]

    def beats_bh(m: dict, b: dict) -> bool:
        return bool(m["net"] > 0 and (m["sharpe"] > b["sharpe"] or m["max_dd"] <= 0.5 * b["max_dd"]))

    report["verdict"] = {}
    for name, m in report["runs"].items():
        if name == "R10a_spot_sim":
            continue
        a_ = bool(m["sharpe"] > R["sharpe"] and m["max_dd"] <= R["max_dd"] + 0.05)
        b_ = beats_bh(m, B)
        c_ = beats_bh(m["costs_x2"], B["costs_x2"])
        liq = m["liquidated"] is None and m["costs_x2"]["liquidated"] is None
        report["verdict"][name] = {"A_beats_R10a": a_, "B_beats_BH": b_, "C_costs_x2": c_, "no_liquidation": liq,
                                   "pass": bool(a_ and b_ and c_ and liq)}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(f"window {a.window} {start}->{end}  data {data_info}")
    for k, v in report["benchmarks"].items():
        print(f"  {k:22s} net {v['net']:+8.1%} sharpe {v['sharpe']:.2f} dd {v['max_dd']:.1%}")
    for k, v in report["runs"].items():
        vd = report["verdict"].get(k, {})
        print(f"  {k:22s} net {v['net']:+8.1%} cagr {v['cagr']:+.1%} sharpe {v['sharpe']:.2f} dd {v['max_dd']:.1%} "
              f"idd {v['max_dd_intraday']:.1%} trades {v['trades']} fees {v['fees']:.0f} fund {v['funding_paid']:+.0f} "
              f"liq {v['liquidated']} x2 {v['costs_x2']['sharpe']:.2f} corr {v.get('corr_with_R10a', float('nan')):.2f} "
              f"{'PASS' if vd.get('pass') else ''} {vd}")


if __name__ == "__main__":
    main()
