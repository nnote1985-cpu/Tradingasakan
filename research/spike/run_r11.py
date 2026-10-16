"""R11: R7 futures long/short with a ±2% hysteresis band on SMA100, pre-declared in
docs/research/r11-futures-band-plan.md (commit 26e7895). Run once; nothing is tuned afterwards.

    PYTHONPATH=research/spike uv run python research/spike/run_r11.py

Reuses run_r7's simulator unchanged; only the side function is swapped for the band variant.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import run_r7
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import daily_bars

DATA = Path("data/binance_um")            # 2022-01-01 -> 2025-12-31 only: no C1/C2 bars loaded
START, END = "2022-04-12", "2026-01-01"
YEARS = [("2022 (Apr-Dec)", "2022-04-12", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
         ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01")]
BAND = 0.02
R7_SIDE = run_r7.side_state


def market() -> tuple[pd.DataFrame, pd.Series]:
    h1_raw = load(DATA, "BTCUSDT", "1h")
    d = daily_bars(h1_raw)
    h1 = regularize_h1(h1_raw)
    g = h1.groupby(h1.index.floor("1D"))
    d["high"], d["low"] = g["high"].max(), g["low"].min()
    d.loc[~d["valid"], ["high", "low"]] = np.nan
    f = load_funding(DATA, "BTCUSDT")
    return d, f.groupby(f.index.floor("1D")).sum()


def band_side(daily: pd.DataFrame) -> pd.Series:
    """+1/-1 latch: flip long above SMA100 x 1.02, short below x 0.98; 0 before SMA100 exists."""
    close = daily["close"]
    sma = close.rolling(run_r7.SMA_N, min_periods=run_r7.SMA_N).mean()
    side: float | None = None
    out = np.zeros(len(close))
    for i, (c, s) in enumerate(zip(close.to_numpy(), sma.to_numpy(), strict=True)):
        if np.isfinite(c) and np.isfinite(s):
            if side is None:
                side = 1.0 if c > s else -1.0 if c < s else 0.0
            elif side != 1.0 and c > s * (1 + BAND):
                side = 1.0
            elif side != -1.0 and c < s * (1 - BAND):
                side = -1.0
        out[i] = side or 0.0
    return pd.Series(out, index=daily.index)


def summary(eq: pd.Series, trades: pd.DataFrame, st: dict) -> dict:
    ruined = bool((eq <= 0).any())
    r = {"net": float(eq.iloc[-1] / run_r7.EQUITY - 1), "sharpe": float("nan") if ruined else sharpe(daily_log_returns(eq)),
         "max_dd": max_drawdown(eq), "flips": int((trades["action"] == "open").sum()) if len(trades) else 0, **st}
    r["years"] = {}
    for y, a, b in YEARS:
        e = eq[(eq.index >= pd.Timestamp(a, tz="UTC") - pd.Timedelta(days=1)) & (eq.index < pd.Timestamp(b, tz="UTC"))]
        r["years"][y] = {"net": float(e.iloc[-1] / e.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(e)), "max_dd": max_drawdown(e)}
    return r


def main() -> None:
    np.seterr(all="ignore")
    daily, funding = market()
    out = Path("reports/r11")
    out.mkdir(parents=True, exist_ok=True)
    bh = run_r7.buy_and_hold(daily, START, END)
    report: dict = {"plan": "docs/research/r11-futures-band-plan.md @ 26e7895", "window": [START, END],
                    "buy_and_hold": {"net": float(bh.iloc[-1] / run_r7.EQUITY - 1), "sharpe": sharpe(daily_log_returns(bh)),
                                     "max_dd": max_drawdown(bh)}, "runs": {}}
    base_fee, base_slip = run_r7.FEE, run_r7.SLIP
    for costs_name, mult in (("base_costs", 1), ("costs_x2", 2)):
        run_r7.FEE, run_r7.SLIP = base_fee * mult, base_slip * mult
        report["runs"][costs_name] = {}
        for name, fn in (("R7", R7_SIDE), ("R11_band", band_side)):
            run_r7.side_state = fn
            eq, trades, st = run_r7.simulate(daily, funding, START, END)
            report["runs"][costs_name][name] = summary(eq, trades, st)
            if costs_name == "base_costs":
                pd.DataFrame({"strategy": eq, "buy_and_hold": bh}).to_csv(out / f"equity_{name}.csv")
                trades.to_csv(out / f"trades_{name}.csv", index=False)
    run_r7.FEE, run_r7.SLIP, run_r7.side_state = base_fee, base_slip, R7_SIDE
    b, r = report["runs"]["base_costs"]["R7"], report["runs"]["base_costs"]["R11_band"]
    h = report["buy_and_hold"]
    report["verdict"] = {
        "better_than_R7": bool(r["sharpe"] > b["sharpe"] and r["max_dd"] <= b["max_dd"] and r["net"] > 0),
        "r7_rule_vs_bh": bool(r["net"] > 0 and r["sharpe"] > h["sharpe"]),
        "note_objective_vs_bh": bool(r["net"] > 0 and (r["sharpe"] > h["sharpe"] or r["max_dd"] <= 0.5 * h["max_dd"])),
    }
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
