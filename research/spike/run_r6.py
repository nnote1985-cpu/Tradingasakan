"""R6: v1 on BTCUSDT spot, long only — pre-declared in docs/research/r6-v1-spot-plan.md.

    uv run python research/spike/run_r6.py development          # both variants
    uv run python research/spike/run_r6.py confirm --variant R6a  # C1 and C2, once, after selection is committed

Downloads checksum-verified spot 1h klines into data/binance_spot/ (cached).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable, regular_grid

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import _download_zip, _months, parse_klines_csv
from trading.risk.sizing import InstrumentRules, RiskProfile, SizingAllowances
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, CostModel, ExitParams
from trading.strategies import trend_breakout_v1 as v1

SPOT = "https://data.binance.vision/data/spot"
SYMBOL = "BTCUSDT"
DATA = Path("data/binance_spot")
EQUITY = 10_000.0
SETS = {
    "development": ("2022-04-12", "2026-01-01"),
    "C1": ("2018-01-01", "2022-01-01"),
    "C2": ("2026-01-01", "2026-10-01"),
}
COSTS = CostModel(taker_fee=0.001, entry_slip_bps=2.0, exit_slip_bps=2.0, stop_slip_bps=5.0)
RULES = InstrumentRules(tick=Decimal("0.01"), qty_step=Decimal("0.00001"), min_notional=Decimal("10"), max_qty=Decimal("1000"))
ALLOW = SizingAllowances(entry_fee_rate=Decimal("0.001"), exit_fee_rate=Decimal("0.001"),
                         stop_slip_fraction=Decimal("0.0005"), funding_reserve_fraction=Decimal("0"))
EXITS = ExitParams(stop_atr_mult=2.0, take_profit_r=None, time_exit=pd.Timedelta(days=365), cooldown=pd.Timedelta(hours=2))
NO_LEVERAGE = dict(max_gross_notional_to_equity=Decimal("1"), requested_exchange_leverage=Decimal("1"),
                   max_margin_utilization_fraction=Decimal("1"))
VARIANTS = {
    "R6a": RiskProfile(**NO_LEVERAGE),
    "R6b": RiskProfile(risk_per_trade_fraction=Decimal("1"), absolute_trade_risk_cap_usdt=Decimal("1e12"),
                       daily_loss_trigger_fraction=Decimal("1"), **NO_LEVERAGE),
}


def load_spot(start: date, end: date) -> tuple[pd.DataFrame, list[dict]]:
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    frames, files = [], []
    for y, m in _months(start, end):
        url = f"{SPOT}/monthly/klines/{SYMBOL}/1h/{SYMBOL}-1h-{y}-{m:02d}.zip"
        inner, info = _download_zip(url, raw)
        if info.get("status") not in ("VERIFIED",):
            raise RuntimeError(f"{url}: {info}")
        files.append(info)
        frames.append(parse_klines_csv(inner))
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="first")], files


def features():
    h1_raw, files = load_spot(date(2017, 8, 1), date(2026, 9, 30))
    h1, h4 = v1.build_features(h1_raw, v1.StrategyParams())
    h1["signal"] = h1["signal"].where(h1["signal"] == "LONG", "NO_TRADE")       # long only
    low10 = h4["low"].rolling(10, min_periods=10).min().shift(1)
    h4["long_exit"] = (h4["close"] < low10) & h4["valid"]
    h4["short_exit"] = False
    return h1_raw, h1, h4, files


def run_set(name: str, variant: str, h1_raw, h1, h4) -> dict:
    s, e = (pd.Timestamp(x, tz="UTC") for x in SETS[name])
    ex = regular_grid(h1_raw, pd.Timedelta(hours=1))
    cfg = BacktestConfig(initial_equity=EQUITY, costs=COSTS, exits=EXITS, profile=VARIANTS[variant], rules=RULES,
                         allowances=ALLOW, halt_policy="record_only")
    res = BarBacktester(ex, h1, h4, None, cfg).run(start=s, end=e)
    eq = res.daily_equity
    # Buy & hold: buy at the first open, same fee and slippage, hold to the last close.
    px = ex.loc[(ex.index >= s) & (ex.index < e)].dropna()
    buy = px["open"].iloc[0] * (1 + COSTS.entry_slip_bps / 1e4)
    qty = EQUITY * (1 - COSTS.taker_fee) / buy
    daily_close = px["close"].groupby(px.index.normalize()).last()
    bh = pd.concat([pd.Series([EQUITY], index=[s]), qty * daily_close.shift(-0)])
    bh.index = list(bh.index[:1]) + [d + pd.Timedelta(days=1) for d in daily_close.index]
    bh = bh[~bh.index.duplicated(keep="last")]
    t = res.trades
    in_mkt = 0.0
    if len(t):
        in_mkt = float(((t["exit_time"] - t["entry_time"]).sum()) / (e - s))
    out = {
        "set": name, "variant": variant, "range": SETS[name],
        "net": float(eq.iloc[-1] / EQUITY - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq),
        "bh_net": float(bh.iloc[-1] / EQUITY - 1), "bh_sharpe": sharpe(daily_log_returns(bh)), "bh_max_dd": max_drawdown(bh),
        "trades": int(len(t)), "time_in_market": in_mkt,
        "avg_notional_to_initial_equity": float((t["qty"] * t["entry_price"]).mean() / EQUITY) if len(t) else 0.0,
        "fees": float(t["fees"].sum()) if len(t) else 0.0,
        "exit_reasons": t["exit_reason"].value_counts().to_dict() if len(t) else {},
        "loss_limit_breaches": len(res.events),
        "missing_bars_in_position": res.stats["incomplete_bars_in_position"],
    }
    out["passes"] = bool(out["net"] > 0 and out["sharpe"] > out["bh_sharpe"])
    return out, res, bh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["development", "confirm"])
    ap.add_argument("--variant", choices=list(VARIANTS))
    ap.add_argument("--out", default="/mnt/project-files/r6-results")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    h1_raw, h1, h4, files = features()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if a.stage == "development":
        runs = [(v, "development") for v in VARIANTS]
    else:
        if not a.variant:
            raise SystemExit("--variant is required for confirm")
        if (out / "confirmation.json").exists():
            raise SystemExit("confirmation sets were already spent; see confirmation.json")
        runs = [(a.variant, "C1"), (a.variant, "C2")]
    report = {"commit": commit, "stage": a.stage, "spot_files": len(files),
              "spot_files_sha256": {f["file"]: f["sha256"] for f in files}, "runs": []}
    for v, s in runs:
        r, res, bh = run_set(s, v, h1_raw, h1, h4)
        report["runs"].append(r)
        res.trades.to_csv(out / f"trades_{v}_{s}.csv", index=False)
        pd.DataFrame({"strategy": res.daily_equity, "buy_and_hold": bh}).to_csv(out / f"equity_{v}_{s}.csv")
    name = "development.json" if a.stage == "development" else "confirmation.json"
    (out / name).write_text(json.dumps(jsonable(report), indent=2))
    for r in report["runs"]:
        print(f"{r['variant']} {r['set']}: net {r['net']:+.1%} sharpe {r['sharpe']:.2f} dd {r['max_dd']:.1%} | "
              f"B&H {r['bh_net']:+.1%} {r['bh_sharpe']:.2f} {r['bh_max_dd']:.1%} | trades {r['trades']} "
              f"in-market {r['time_in_market']:.0%} size {r["avg_notional_to_initial_equity"]:.0%} -> {'PASS' if r['passes'] else 'fail'}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
