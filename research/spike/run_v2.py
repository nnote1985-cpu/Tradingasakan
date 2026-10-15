"""v2 trend regime vs buy & hold on BTC/ETH/SOL — runs pre-declared in docs/research/v2-plan.md.

    uv run python research/spike/run_v2.py [--out reports/v2]

Development and validation only; the holdout is never simulated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from common import jsonable, load_config, partitions
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars, regime_state, simulate

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
EQUITY = 10_000.0
SMA_DEFAULT = 100
FEES = (0.0005, 0.0010, 0.0020)
SLIPS = (0.0, 2.0, 10.0)
PERP = RegimeCosts(fee=0.0005, slip_bps=2.0)


def metrics(eq: pd.Series, n_books: int = 1) -> dict:
    r = daily_log_returns(eq)
    return {"net_return": float(eq.iloc[-1] / (EQUITY * n_books) - 1), "sharpe": sharpe(r),
            "max_dd": max_drawdown(eq)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/v2")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    data = {}
    for s in SYMBOLS:
        d = Path(cfg["data_dir"] if s == "BTCUSDT" else f"{cfg['data_dir']}_{s}")
        h1 = load(d, s, "1h")
        f = load_funding(d, s)
        data[s] = {"h1": h1, "daily": daily_bars(h1),
                   "funding": f.groupby(f.index.floor("1D")).sum() if f is not None else None}
    parts = partitions(data["BTCUSDT"]["h1"], cfg["partitions"], pd.Timedelta(hours=4 * 600))
    parts = {k: v for k, v in parts.items() if k != "holdout"}

    report = {"partitions": {k: [str(a), str(b)] for k, (a, b) in parts.items()},
              "holdout_simulated": False, "runs": {}, "per_coin": {}, "cost_grid": {}, "diagnostics": {}}

    def book_runs(sma, costs, part, always_in=False, perp=False, start_override=None):
        s, e = parts[part]
        if start_override is not None:
            s = max(s, start_override)
        eqs, info = [], {}
        for sym in SYMBOLS:
            daily = data[sym]["daily"]
            st = pd.Series(True, index=daily.index) if always_in else regime_state(daily, sma)
            eq, tr = simulate(daily, st, s, e, equity=EQUITY, costs=costs,
                              funding_daily=data[sym]["funding"] if perp else None)
            eqs.append(eq)
            info[sym] = {**metrics(eq), "trades": int(len(tr)),
                         "time_in_market": float(st.shift(1).reindex(eq.index[1:]).fillna(False).mean())}
        return sum(eqs), info

    default = RegimeCosts()
    for part in parts:
        for name, kw in (("v2", {}), ("buy_and_hold", {"always_in": True})):
            eq, info = book_runs(SMA_DEFAULT, default, part, **kw)
            report["runs"][f"{name}/{part}"] = metrics(eq, len(SYMBOLS))
            report["per_coin"][f"{name}/{part}"] = info
            eq.to_csv(out / f"equity_{name}_{part}.csv")
        v2 = pd.read_csv(out / f"equity_v2_{part}.csv", index_col=0, parse_dates=True).iloc[:, 0]
        bh = pd.read_csv(out / f"equity_buy_and_hold_{part}.csv", index_col=0, parse_dates=True).iloc[:, 0]
        m = pd.DataFrame({"v2": v2.resample("ME").last(), "bh": bh.resample("ME").last()})
        m = pd.concat([pd.DataFrame({"v2": [v2.iloc[0]], "bh": [bh.iloc[0]]}, index=[v2.index[0]]), m]).pct_change().iloc[1:]
        m.to_csv(out / f"monthly_{part}.csv")

        for fee in FEES:
            for slip in SLIPS:
                eq, _ = book_runs(SMA_DEFAULT, RegimeCosts(fee=fee, slip_bps=slip), part)
                report["cost_grid"][f"{part}/fee{fee}/slip{slip}"] = metrics(eq, len(SYMBOLS))

        eq, info = book_runs(SMA_DEFAULT, PERP, part, perp=True)
        report["diagnostics"][f"perp_with_funding/{part}"] = metrics(eq, len(SYMBOLS))
        eq, _ = book_runs(50, default, part)
        report["diagnostics"][f"sma50/{part}"] = metrics(eq, len(SYMBOLS))
        s150 = pd.Timestamp("2022-06-01", tz="UTC")
        eq, _ = book_runs(150, default, part, start_override=s150)
        report["diagnostics"][f"sma150/{part}"] = metrics(eq, len(SYMBOLS))
        eq, _ = book_runs(150, default, part, always_in=True, start_override=s150)
        report["diagnostics"][f"buy_and_hold_sma150_window/{part}"] = metrics(eq, len(SYMBOLS))

    verdict = {}
    for part in parts:
        v, b = report["runs"][f"v2/{part}"], report["runs"][f"buy_and_hold/{part}"]
        verdict[part] = {"net_positive": v["net_return"] > 0, "sharpe_above_bh": v["sharpe"] > b["sharpe"],
                         "dd_at_most_half_bh": v["max_dd"] <= 0.5 * b["max_dd"]}
        verdict[part]["pass"] = verdict[part]["net_positive"] and (
            verdict[part]["sharpe_above_bh"] or verdict[part]["dd_at_most_half_bh"])
    verdict["PASS"] = all(verdict[p]["pass"] for p in parts)
    report["verdict"] = verdict
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))

    print(pd.DataFrame(report["runs"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    for k, v in report["per_coin"].items():
        print(k, pd.DataFrame(v).T.round(4).to_dict("index"))
    print(pd.DataFrame(report["cost_grid"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    print(pd.DataFrame(report["diagnostics"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    print(json.dumps(jsonable(verdict), indent=2))


if __name__ == "__main__":
    main()
