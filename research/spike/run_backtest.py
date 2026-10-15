"""Phase 0.5 standalone backtest of trend_breakout_v1.0 (BUILDPLAN v4.2 §4.3).

    uv run python research/spike/run_backtest.py [--config configs/spike.json] [--out reports/spike]

Runs on development and validation only. The holdout boundary is written to the
report but no holdout bar is simulated here. Every grid trial is kept in
trials.csv, including the bad ones.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable, load_config, load_market, make_backtester, partitions, with_exits

from trading.evaluation.metrics import daily_log_returns, summary
from trading.simulator.bar_backtest import BacktestConfig, CostModel
from trading.strategies.trend_breakout_v1 import STRATEGY_ID, StrategyParams, build_features


def run_one(bt, start, end, prices, equity) -> tuple[dict, object]:
    res = bt.run(start=start, end=end)
    return summary(res, initial_equity=equity, prices=prices), res


def monthly_table(res, prices: pd.Series, equity: float) -> pd.DataFrame:
    eq = res.daily_equity.resample("ME").last()
    eq = pd.concat([pd.Series([equity], index=[res.daily_equity.index[0] - pd.Timedelta(days=1)]), eq])
    px = prices.loc[res.stats["start"]:res.stats["end"]].resample("ME").last()
    px = pd.concat([pd.Series([prices.loc[res.stats["start"]:].iloc[0]],
                              index=[res.daily_equity.index[0] - pd.Timedelta(days=1)]), px])
    out = pd.DataFrame({"v1_return": eq.pct_change(), "bh_return": px.pct_change()}).iloc[1:]
    out["v1_beats_bh"] = out["v1_return"] > out["bh_return"]
    return out


def trade_diagnostics(trades: pd.DataFrame, h1: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    r = trades["r_multiple"]
    sig = pd.DatetimeIndex(trades["signal_time"]) - pd.Timedelta(hours=1)
    atr_pct = (h1["atr"] / h1["close"]).reindex(sig).to_numpy()
    terciles = pd.qcut(atr_pct, 3, labels=["low_vol", "mid_vol", "high_vol"], duplicates="drop") if np.isfinite(atr_pct).sum() >= 3 else None
    by = lambda key: trades.assign(k=key).groupby("k", observed=True)["r_multiple"].agg(["count", "mean", "median"]).round(4).to_dict("index")
    return {
        "r_quantiles": r.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(4).to_dict(),
        "hold_hours_quantiles": trades["hold_hours"].quantile([0.25, 0.5, 0.75, 0.95]).round(2).to_dict(),
        "r_lag1_autocorr": float(r.autocorr(1)) if len(r) > 2 else None,
        "by_side": by(trades["side"].to_numpy()),
        "by_quarter": by(pd.DatetimeIndex(trades["entry_time"]).tz_localize(None).to_period("Q").astype(str)),
        "by_vol_tercile": by(np.asarray(terciles)) if terciles is not None else {},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/spike")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    market = load_market(cfg)
    params = StrategyParams()
    features = build_features(market["h1"], params)
    h1f = features[0]
    prices = market["h1"]["close"]
    equity = float(cfg["initial_equity"])
    base_cfg = BacktestConfig(initial_equity=equity)
    warmup = pd.Timedelta(hours=4 * params.warmup_4h_bars)
    parts = partitions(market["h1"], cfg["partitions"], warmup)

    report: dict = {
        "strategy_id": STRATEGY_ID, "symbol": cfg["symbol"],
        "exec_interval": market["exec_interval"],
        "funding_supplied": market["funding"] is not None,
        "data": {"h1": [str(market["h1"].index[0]), str(market["h1"].index[-1]), len(market["h1"])],
                 "exec_bars": len(market["exec"]),
                 "exec_bars_missing": int(market["exec"]["open"].isna().sum()),
                 "h1_invalid_bars": int((~h1f["valid"]).sum())},
        "partitions": {k: [str(a), str(b)] for k, (a, b) in parts.items()},
        "holdout_simulated": False,
        "costs_default": base_cfg.costs.__dict__,
        "runs": {}, "diagnostics": {},
    }
    trials = []

    # 1. Base runs: ambiguity bounds × halt policy, on development and validation.
    for part in ("development", "validation"):
        start, end = parts[part]
        for amb in ("conservative", "optimistic"):
            for halt in ("absorbing", "record_only"):
                c = replace(base_cfg, ambiguity=amb, halt_policy=halt)
                bt = make_backtester(market, config=c, features=features)
                s, res = run_one(bt, start, end, prices, equity)
                key = f"{part}/{amb}/{halt}"
                report["runs"][key] = s
                trials.append({"trial": "base", "partition": part, "ambiguity": amb, "halt": halt, **_flat(s)})
                if amb == "conservative" and halt == "absorbing":
                    res.trades.to_csv(out / f"trades_{part}.csv", index=False)
                    res.candidates.to_csv(out / f"candidates_{part}.csv", index=False)
                    res.daily_equity.to_csv(out / f"daily_equity_{part}.csv")
                    monthly_table(res, prices, equity).to_csv(out / f"monthly_{part}.csv")
                    report["diagnostics"][part] = trade_diagnostics(res.trades, h1f)
                    if len(res.daily_equity) > 2:
                        r = daily_log_returns(res.daily_equity)
                        report["diagnostics"][part]["daily_return_lag1_autocorr"] = float(r.autocorr(1))

    # 2. Cost sensitivity (pre-declared grid), development and validation.
    grid = cfg["cost_grid"]
    for part in ("development", "validation"):
        start, end = parts[part]
        for fm in grid["fee_mult"]:
            for sm in grid["slip_mult"]:
                c = replace(base_cfg, costs=CostModel().scaled(fm, sm))
                s, _ = run_one(make_backtester(market, config=c, features=features), start, end, prices, equity)
                trials.append({"trial": "cost", "partition": part, "fee_mult": fm, "slip_mult": sm, **_flat(s)})

    # 3. Parameter sensitivity, one axis at a time around the default, development only.
    start, end = parts["development"]
    pg = cfg["param_grid"]
    for axis in ("stop_atr_mult", "take_profit_r", "time_exit_hours"):
        for v in pg[axis]:
            c = with_exits(base_cfg, **{axis: v})
            s, _ = run_one(make_backtester(market, config=c, features=features), start, end, prices, equity)
            trials.append({"trial": "param", "partition": "development", "axis": axis, "value": v, **_flat(s)})
    for v in pg["breakout_lookback"]:
        p = replace(params, breakout_lookback=int(v))
        s, _ = run_one(make_backtester(market, params=p, config=base_cfg), start, end, prices, equity)
        trials.append({"trial": "param", "partition": "development", "axis": "breakout_lookback", "value": v, **_flat(s)})

    pd.DataFrame(trials).to_csv(out / "trials.csv", index=False)
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    _print_headline(report)


HEADLINE = ("net_return", "bh_net_return", "sharpe_daily_365", "bh_sharpe_daily_365",
            "max_drawdown_intraday", "trades", "trades_per_month", "win_rate",
            "expectancy_r", "profit_factor", "time_in_market")


def _flat(s: dict) -> dict:
    return {k: s.get(k) for k in HEADLINE + ("final_equity", "total_fees", "total_funding", "ambiguous_bars")}


def _print_headline(report: dict) -> None:
    rows = {k: {m: v.get(m) for m in HEADLINE} for k, v in report["runs"].items()}
    print(pd.DataFrame(rows).T.to_string(float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
