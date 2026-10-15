"""One-time holdout test of pathA-v1, pre-declared in docs/research/pathA-v1-holdout-plan.md.

    PYTHONPATH=<frozen worktree>/backend/src uv run python research/spike/run_pathA_holdout.py

Run once, with the signal code from frozen/pathA-v1. Nothing is tuned afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from common import jsonable, load_config, partitions

from trading.alerts.risk_check import signals
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import daily_bars

EQUITY = 10_000.0


def metrics(eq: pd.Series) -> dict:
    return {"net_return": float(eq.iloc[-1] / EQUITY - 1), "sharpe": sharpe(daily_log_returns(eq)),
            "max_dd": max_drawdown(eq)}


def main() -> None:
    import trading.alerts.risk_check as rc
    cfg = load_config("configs/spike.json")
    d = Path(cfg["data_dir"])
    h1 = load(d, "BTCUSDT", "1h")
    daily = daily_bars(h1)
    f = load_funding(d, "BTCUSDT")
    w = signals(daily, f.groupby(f.index.floor("1D")).sum())["suggested_btc_weight"]
    s, e = partitions(h1, cfg["partitions"], pd.Timedelta(hours=4 * 600))["holdout"]
    days = daily.index[(daily.index >= s) & (daily.index < e)]
    missing = int(w.shift(1).reindex(days).isna().sum())
    eq, tr = simulate_weights(daily, w.fillna(1.0), s, e, equity=EQUITY)
    bh, _ = simulate_weights(daily, pd.Series(1.0, index=daily.index), s, e, equity=EQUITY)
    v, b = metrics(eq), metrics(bh)
    verdict = {"net_positive": v["net_return"] > 0, "sharpe_above_bh": v["sharpe"] > b["sharpe"],
               "dd_at_most_half_bh": v["max_dd"] <= 0.5 * b["max_dd"]}
    verdict["PASS"] = verdict["net_positive"] and (verdict["sharpe_above_bh"] or verdict["dd_at_most_half_bh"])
    held = w.shift(1).reindex(days).fillna(1.0)
    report = {"signal_code": rc.__file__, "holdout": [str(s), str(e)], "pathA_v1": v, "buy_and_hold": b,
              "trades": int(len(tr)), "days_missing_signal": missing,
              "share_days_out_of_btc": float((held == 0).mean()), "share_days_half": float((held == 0.5).mean()),
              "verdict": verdict}
    out = Path("reports/pathA_holdout")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    eq.to_csv(out / "equity_pathA_v1.csv")
    bh.to_csv(out / "equity_buy_and_hold.csv")
    tr.to_csv(out / "trades_pathA_v1.csv", index=False)
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
