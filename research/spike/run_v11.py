"""v1 vs v1.1 on BTC/ETH/SOL — runs pre-declared in docs/research/v1.1-plan.md.

    uv run python research/spike/run_v11.py [--out reports/v11]

Development and validation only; the holdout is never simulated. Each symbol
is its own 10,000 USDT book and the portfolio is the sum of the books.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from common import jsonable, load_config, load_market, partitions
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.risk.sizing import InstrumentRules
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, CostModel, ExitParams
from trading.strategies import trend_breakout_v1 as v1
from trading.strategies import trend_donchian_4h_v1_1 as v11

SYMBOLS = {
    # APPROXIMATE exchange filters — verify before live use (§4.1).
    "BTCUSDT": InstrumentRules(tick=Decimal("0.1"), qty_step=Decimal("0.001")),
    "ETHUSDT": InstrumentRules(tick=Decimal("0.01"), qty_step=Decimal("0.001")),
    "SOLUSDT": InstrumentRules(tick=Decimal("0.01"), qty_step=Decimal("1"), max_qty=Decimal("1000000")),
}
EQUITY = 10_000.0
V11_EXITS = ExitParams(stop_atr_mult=2.0, take_profit_r=None,
                       time_exit=pd.Timedelta(days=60), cooldown=pd.Timedelta(hours=4))
COST_GRID = {"fee_mult": [0.4, 1.0, 2.0], "slip_mult": [0.0, 1.0, 2.0, 4.0]}
SENS = [("entry_lookback", 55), ("exit_lookback", 20), ("stop_atr_mult", 3.0)]


def _row(res, months) -> dict:
    t = res.trades
    out = {"final_equity": float(res.daily_equity.iloc[-1]), "trades": int(len(t)),
           "trades_per_month": len(t) / months if months else np.nan}
    if len(t):
        out.update(expectancy_r=float(t["r_multiple"].mean()), win_rate=float((t["net_pnl"] > 0).mean()),
                   gross_r=float((t["gross_pnl"] / t["initial_risk"]).mean()),
                   fee_r=float((t["fees"] / t["initial_risk"]).mean()),
                   gross_pnl=float(t["gross_pnl"].sum()), fees=float(t["fees"].sum()),
                   funding=float(t["funding"].sum()),
                   median_hold_h=float(t["hold_hours"].median()),
                   best_r=float(t["r_multiple"].max()))
    return out


def run_symbol(args) -> dict:
    symbol, base_cfg, parts = args
    cfg = dict(base_cfg, symbol=symbol,
               data_dir=base_cfg["data_dir"] if symbol == "BTCUSDT" else f"{base_cfg['data_dir']}_{symbol}")
    market = load_market(cfg)
    rules = SYMBOLS[symbol]
    f_v1 = v1.build_features(market["h1"], v1.StrategyParams())
    f_v11 = v11.build_features(market["h1"], v11.DonchianParams())

    def bt(features, exits=None, costs=None, halt="absorbing"):
        c = BacktestConfig(initial_equity=EQUITY, rules=rules, halt_policy=halt)
        if exits is not None:
            c = replace(c, exits=exits)
        if costs is not None:
            c = replace(c, costs=costs)
        return BarBacktester(market["exec"], features[0], features[1], market["funding"], c)

    out = {"symbol": symbol, "base": {}, "equity": {}, "trades": {}, "cost": [], "sens": []}
    for part in ("development", "validation"):
        s, e = parts[part]
        months = (e - s) / pd.Timedelta(days=30.4375)
        for name, feats, exits in (("v1", f_v1, None), ("v1.1", f_v11, V11_EXITS)):
            for halt in ("absorbing", "record_only"):
                res = bt(feats, exits, halt=halt).run(start=s, end=e)
                key = f"{name}/{part}/{halt}"
                out["base"][key] = _row(res, months)
                out["equity"][key] = res.daily_equity
                if halt == "record_only":
                    out["trades"][key] = res.trades.assign(symbol=symbol, strategy=name, partition=part)
        for fm in COST_GRID["fee_mult"]:
            for sm in COST_GRID["slip_mult"]:
                res = bt(f_v11, V11_EXITS, CostModel().scaled(fm, sm), halt="record_only").run(start=s, end=e)
                out["cost"].append({"partition": part, "fee_mult": fm, "slip_mult": sm,
                                    "final_equity": float(res.daily_equity.iloc[-1]),
                                    **{k: v for k, v in _row(res, months).items() if k in ("trades", "expectancy_r")}})
    s, e = parts["development"]
    months = (e - s) / pd.Timedelta(days=30.4375)
    for axis, val in SENS:
        if axis == "stop_atr_mult":
            feats, exits = f_v11, replace(V11_EXITS, stop_atr_mult=val)
        else:
            feats = v11.build_features(market["h1"], replace(v11.DonchianParams(), **{axis: val}))
            exits = V11_EXITS
        res = bt(feats, exits, halt="record_only").run(start=s, end=e)
        out["sens"].append({"axis": axis, "value": val, **_row(res, months)})

    prices = market["h1"]["close"]
    out["bh"] = {}
    for part in ("development", "validation"):
        s, e = parts[part]
        p = prices.loc[s:e].resample("1D").last().dropna()
        out["bh"][part] = EQUITY * (1 - 0.0005) * p / p.iloc[0]
    return out


def port_metrics(eq: pd.Series, n_books: int) -> dict:
    init = EQUITY * n_books
    r = daily_log_returns(eq)
    return {"net_return": float(eq.iloc[-1] / init - 1), "sharpe": sharpe(r),
            "max_dd": max_drawdown(eq)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/v11")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    btc = load_market(cfg)
    parts = partitions(btc["h1"], cfg["partitions"], pd.Timedelta(hours=4 * 600))
    del btc
    with ProcessPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(run_symbol, [(s, cfg, parts) for s in SYMBOLS]))

    report = {"partitions": {k: [str(a), str(b)] for k, (a, b) in parts.items()},
              "holdout_simulated": False, "per_symbol": {}, "portfolio": {}, "bh": {}}
    for r in results:
        report["per_symbol"][r["symbol"]] = r["base"]
    n = len(results)
    for key in results[0]["equity"]:
        eq = sum(r["equity"][key] for r in results)
        t = pd.concat([r["trades"][key] for r in results if key in r["trades"]]) if key.endswith("record_only") else None
        m = port_metrics(eq, n)
        if t is not None and len(t):
            m.update(trades=int(len(t)), expectancy_r=float(t["r_multiple"].mean()),
                     gross_r=float((t["gross_pnl"] / t["initial_risk"]).mean()),
                     fee_r=float((t["fees"] / t["initial_risk"]).mean()),
                     win_rate=float((t["net_pnl"] > 0).mean()))
        else:
            m["trades"] = sum(r["base"][key]["trades"] for r in results)
        report["portfolio"][key] = m
        eq.to_csv(out / f"equity_{key.replace('/', '_')}.csv")
    for part in ("development", "validation"):
        bh = sum(r["bh"][part] for r in results)
        report["bh"][f"equal_weight/{part}"] = port_metrics(bh, n)
        btc_bh = next(r for r in results if r["symbol"] == "BTCUSDT")["bh"][part]
        report["bh"][f"BTC/{part}"] = port_metrics(btc_bh, 1)
        # Monthly portfolio vs equal-weight B&H for v1.1 record_only.
        eq = sum(r["equity"][f"v1.1/{part}/record_only"] for r in results)
        mth = pd.DataFrame({"v1.1": eq.resample("ME").last(), "bh": bh.resample("ME").last()})
        first = pd.DataFrame({"v1.1": [EQUITY * n], "bh": [EQUITY * n]}, index=[eq.index[0] - pd.Timedelta(days=1)])
        mth = pd.concat([first, mth]).pct_change().iloc[1:]
        mth["v1.1_beats_bh"] = mth["v1.1"] > mth["bh"]
        mth.to_csv(out / f"monthly_v11_{part}.csv")

    trades = pd.concat([t for r in results for t in r["trades"].values()])
    trades.to_csv(out / "trades.csv", index=False)
    cost = pd.DataFrame([dict(c, symbol=r["symbol"]) for r in results for c in r["cost"]])
    cost.to_csv(out / "cost_grid_by_symbol.csv", index=False)
    cp = cost.groupby(["partition", "fee_mult", "slip_mult"])["final_equity"].sum() / (EQUITY * n) - 1
    cp.rename("portfolio_net_return").to_csv(out / "cost_grid_portfolio.csv")
    sens = pd.DataFrame([dict(s, symbol=r["symbol"]) for r in results for s in r["sens"]])
    sens.to_csv(out / "sensitivity_dev.csv", index=False)
    sp = sens.groupby(["axis", "value"]).agg(final_equity=("final_equity", "sum"), trades=("trades", "sum"))
    sp["portfolio_net_return"] = sp["final_equity"] / (EQUITY * n) - 1
    report["sensitivity_dev_portfolio"] = sp.reset_index().to_dict("records")
    report["cost_grid_portfolio"] = {f"{p}/fee{f}/slip{s}": v for (p, f, s), v in cp.items()}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))

    pf = pd.DataFrame(report["portfolio"]).T
    print(pf.to_string(float_format=lambda x: f"{x:.4f}"))
    print(pd.DataFrame(report["bh"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    print(cp.unstack().to_string(float_format=lambda x: f"{x:.4f}"))
    print(sp.to_string(float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
