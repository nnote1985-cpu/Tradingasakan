"""R13: screen of 8 open-source EAs (Argus, MIT) on dev data, pre-declared in
docs/research/r13-open-source-ea-screen-plan.md. Run once; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r13.py

Loads only data/binance_um (2022-01-01 -> 2025-12-31). C1 and C2 are not loaded.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import run_r7
import run_r11
from common import jsonable
from ea_ports import EAS, PERP, SPOT, Costs, simulate
from run_r10 import latch

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

DATA = Path("data/binance_um")
START, END = "2022-05-01", "2026-01-01"
YEARS = [("2022 May-Dec", "2022-05-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
         ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]
OUT = Path("reports/r13")


def metrics(eq: pd.Series) -> dict:
    eq = eq.copy()
    eq.index = pd.DatetimeIndex(eq.index).tz_localize(None) if pd.DatetimeIndex(eq.index).tz else eq.index
    r = {"net": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}
    r["years"] = {}
    for y, a, b in YEARS:
        e = eq[(eq.index >= pd.Timestamp(a) - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b))]
        r["years"][y] = float(e.iloc[-1] / e.iloc[0] - 1)
    return r


def trade_stats(tr: pd.DataFrame, eq_h: pd.Series) -> dict:
    if tr.empty:
        return {"trades": 0}
    pnl = tr["equity_after"] - tr["equity_before"]
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    held = sum(((pd.Timestamp(x) - pd.Timestamp(y)).total_seconds() for x, y in zip(tr["exit_time"], tr["entry_time"], strict=True)))
    span = (eq_h.index[-1] - eq_h.index[0]).total_seconds() + 3600
    return {"trades": int(len(tr)), "longs": int((tr["side"] > 0).sum()), "shorts": int((tr["side"] < 0).sum()),
            "win_rate": float((pnl > 0).mean()), "profit_factor": float(wins / losses) if losses > 0 else None,
            "time_in_market": held / span,
            "avg_notional_pct": float((tr["notional"] / tr["equity_before"]).mean()),
            "exits": tr["reason"].value_counts().to_dict()}


def load_all():
    h1 = load(DATA, "BTCUSDT", "1h")
    m1 = load(DATA, "BTCUSDT", "1m")
    f = load_funding(DATA, "BTCUSDT")
    assert h1.index[-1] < pd.Timestamp("2026-01-01", tz="UTC") and m1.index[-1] < pd.Timestamp("2026-01-01", tz="UTC")
    assert len(h1) == 35064 and len(m1) == 2103840, "expect complete 1h/1m grids 2022-2025"
    return h1, m1, f


def one(args):
    k, market, sizing, cost_name = args
    h1, m1, f = load_all()
    base = SPOT if market == "spot" else PERP
    costs = base if cost_name == "base" else Costs(fee=base.fee * 2, slip=base.slip * 2, stop_slip=base.stop_slip * 2)
    ea = EAS[k]
    daily, tr, eq_h = simulate(ea, h1, m1, f, start=START, end=END, market=market, sizing=sizing, costs=costs)
    return (ea.name, market, sizing, cost_name), daily, tr, eq_h


def baselines():
    h1 = load(DATA, "BTCUSDT", "1h")
    daily = daily_bars(h1)
    daily.index = daily.index.tz_localize(None)
    f = load_funding(DATA, "BTCUSDT")
    f.index = f.index.tz_localize(None)
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    c, sma = sig["close"], sig["sma100"]
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * 1.02, lambda i: c.iloc[i] < sma.iloc[i] * 0.98)
    out = {}
    for cn, rc in (("base", RegimeCosts()), ("x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        out[("R10a", cn)], _ = simulate_weights(daily, band * fund, START, END, equity=10_000.0, costs=rc)
        out[("BH_spot", cn)], _ = simulate_weights(daily, pd.Series(1.0, index=sig.index), START, END, equity=10_000.0, costs=rc)
    d11, fund11 = run_r11.market()
    base_fee, base_slip = run_r7.FEE, run_r7.SLIP
    for cn, mult in (("base", 1), ("x2", 2)):
        run_r7.FEE, run_r7.SLIP = base_fee * mult, base_slip * mult
        run_r7.side_state = run_r11.band_side
        eq, _, _ = run_r7.simulate(d11, fund11, START, END)
        eq.index = eq.index.tz_localize(None)
        out[("R11", cn)] = eq
    run_r7.FEE, run_r7.SLIP, run_r7.side_state = base_fee, base_slip, run_r11.R7_SIDE
    return out


def main() -> None:
    np.seterr(all="ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    base = baselines()
    bench = {f"{k[0]}|{k[1]}": metrics(v) for k, v in base.items()}
    r10a_ret = daily_log_returns(base[("R10a", "base")])
    jobs = [(k, m, s, c) for k in range(len(EAS)) for m in ("spot", "perp") for s in ("native", "full") for c in ("base", "x2")]
    runs = {}
    with ProcessPoolExecutor(max_workers=4) as ex:
        for key, daily, tr, eq_h in ex.map(one, jobs):
            name, market, sizing, cn = key
            r = metrics(daily) | trade_stats(tr, eq_h)
            d = daily.copy()
            d.index = d.index.tz_localize(None)
            r["corr_r10a"] = float(daily_log_returns(d).corr(r10a_ret))
            runs["|".join(key)] = r
            if cn == "base":
                d.to_csv(OUT / f"equity_{name}_{market}_{sizing}.csv")
                tr.to_csv(OUT / f"trades_{name}_{market}_{sizing}.csv", index=False)
            print(key, {k: r[k] for k in ("net", "sharpe", "max_dd", "trades")}, flush=True)

    bh_s = bench["BH_spot|base"]["sharpe"]
    r10a = bench["R10a|base"]
    verdict = {}
    for ea in EAS:
        p = runs[f"{ea.name}|spot|native|base"]
        x2 = runs[f"{ea.name}|spot|native|x2"]
        yrs_pos = sum(v > 0 for v in p["years"].values())
        c1 = p["net"] > 0 and p["sharpe"] > bh_s
        c2 = x2["net"] > 0 and p["trades"] >= 30 and yrs_pos >= 3
        c3 = p["sharpe"] >= r10a["sharpe"]
        verdict[ea.name] = {"1_profit_and_beats_bh": c1, "2_robust": c2, "3_sharpe_ge_r10a": c3,
                            "years_positive": yrs_pos, "eligible_finalist": bool(c1 and c2),
                            "low_corr_r10a": bool(p.get("corr_r10a", 1) < 0.5)}
    ranking = sorted((ea.name for ea in EAS), key=lambda n: -(runs[f"{n}|spot|native|base"]["sharpe"] or -9))
    report = {"plan": "docs/research/r13-open-source-ea-screen-plan.md", "window": [START, END],
              "benchmarks": bench, "runs": runs, "verdict": verdict, "ranking_spot_native": ranking,
              "c1_c2_loaded": False}
    (OUT / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable({"benchmarks": bench, "verdict": verdict, "ranking": ranking}), indent=2))


if __name__ == "__main__":
    main()
