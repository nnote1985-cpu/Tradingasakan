"""C1 confirmation of R10a (vs path A, spot) and R11 (vs R7, perp), pre-declared in
docs/research/c1-confirm-band-plan.md (commit 1ec62d7). C1 is spent by this run; do not re-run it
with any change.

    uv run python -c "from datetime import date; from pathlib import Path; \
        from trading.market_data.binance_public import download; \
        download('BTCUSDT', date(2020,1,1), date(2021,12,31), Path('data/binance_um_c1'), intervals=('1h',))"
    PYTHONPATH=research/spike uv run python research/spike/run_c1_band.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import run_r7
import run_r11
from common import jsonable
from run_r10 import BAND, latch

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars

DATA = Path("data/binance_um_c1")          # 2020-01-01 -> 2021-12-31 only
START, END = "2020-04-15", "2022-01-01"
YEARS = [("2020 (from Apr 15)", "2020-04-15", "2021-01-01"), ("2021", "2021-01-01", "2022-01-01")]
EQUITY = 10_000.0


def metrics(eq: pd.Series) -> dict:
    ruined = bool((eq <= 0).any())
    return {"net": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": float("nan") if ruined else sharpe(daily_log_returns(eq)),
            "max_dd": max_drawdown(eq)}


def by_year(eq: pd.Series, tz: str | None) -> dict:
    out = {}
    for y, a, b in YEARS:
        lo, hi = pd.Timestamp(a, tz=tz) - pd.Timedelta(days=1), pd.Timestamp(b, tz=tz)
        out[y] = metrics(eq[(eq.index >= lo) & (eq.index < hi)])
    return out


def spot(report: dict, out: Path) -> None:
    daily = daily_bars(load(DATA, "BTCUSDT", "1h"))
    daily.index = daily.index.tz_localize(None) if daily.index.tz is not None else daily.index
    f = load_funding(DATA, "BTCUSDT")
    f.index = f.index.tz_localize(None) if f.index.tz is not None else f.index
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    c, sma = sig["close"], sig["sma100"]
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND))
    w = {"pathA": sig["suggested_btc_weight"], "R10a_band": band * fund, "buy_and_hold": pd.Series(1.0, index=sig.index)}
    days = sig.index
    in_win = (days >= pd.Timestamp(START)) & (days < pd.Timestamp(END))
    report["spot_data"] = {"days": int(in_win.sum()), **{f"{k}_missing": int(v[in_win].isna().sum()) for k, v in w.items()},
                           "funding_hot_days": int((sig["funding_hot"][in_win] == True).sum())}  # noqa: E712
    for costs_name, costs in (("base_costs", RegimeCosts()), ("costs_x2", RegimeCosts(fee=0.002, slip_bps=4.0))):
        for name, ws in w.items():
            eq, tr = simulate_weights(daily, ws, START, END, equity=EQUITY, costs=costs)
            r = metrics(eq) | {"trades": int(len(tr)), "years": by_year(eq, None)}
            report["runs"][costs_name][f"spot_{name}"] = r
            if costs_name == "base_costs":
                eq.to_csv(out / f"equity_spot_{name}.csv")


def futures(report: dict, out: Path) -> None:
    run_r11.DATA = DATA
    daily, funding = run_r11.market()
    bh = run_r7.buy_and_hold(daily, START, END)
    report["runs"]["base_costs"]["perp_buy_and_hold"] = metrics(bh) | {"years": by_year(bh, "UTC")}
    base_fee, base_slip, r7_side = run_r7.FEE, run_r7.SLIP, run_r7.side_state
    for costs_name, mult in (("base_costs", 1), ("costs_x2", 2)):
        run_r7.FEE, run_r7.SLIP = base_fee * mult, base_slip * mult
        for name, fn in (("R7", r7_side), ("R11_band", run_r11.band_side)):
            run_r7.side_state = fn
            eq, trades, st = run_r7.simulate(daily, funding, START, END)
            r = metrics(eq) | {"flips": int((trades["action"] == "open").sum()) if len(trades) else 0, **st,
                               "years": by_year(eq, "UTC")}
            report["runs"][costs_name][f"perp_{name}"] = r
            if costs_name == "base_costs":
                eq.to_csv(out / f"equity_perp_{name}.csv")
                trades.to_csv(out / f"trades_perp_{name}.csv", index=False)
    run_r7.FEE, run_r7.SLIP, run_r7.side_state = base_fee, base_slip, r7_side


def main() -> None:
    np.seterr(all="ignore")
    out = Path("reports/c1_band")
    if (out / "report.json").exists():
        raise SystemExit("C1 was already spent by this run; see reports/c1_band/report.json")
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"plan": "docs/research/c1-confirm-band-plan.md @ 1ec62d7", "window": [START, END],
                    "runs": {"base_costs": {}, "costs_x2": {}}}
    spot(report, out)
    futures(report, out)
    r = report["runs"]["base_costs"]

    def better(new: dict, base: dict) -> bool:
        return bool(new["sharpe"] > base["sharpe"] and new["max_dd"] <= base["max_dd"] and new["net"] > 0)

    def objective(s: dict, bh: dict) -> bool:
        return bool(s["net"] > 0 and (s["sharpe"] > bh["sharpe"] or s["max_dd"] <= 0.5 * bh["max_dd"]))

    report["verdict"] = {
        "R10a_confirmed": better(r["spot_R10a_band"], r["spot_pathA"]),
        "R11_confirmed": better(r["perp_R11_band"], r["perp_R7"]),
        "objective_vs_bh": {"pathA": objective(r["spot_pathA"], r["spot_buy_and_hold"]),
                            "R10a": objective(r["spot_R10a_band"], r["spot_buy_and_hold"]),
                            "R7": objective(r["perp_R7"], r["perp_buy_and_hold"]),
                            "R11": objective(r["perp_R11_band"], r["perp_buy_and_hold"])},
    }
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
