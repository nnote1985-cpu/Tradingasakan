"""How long must the path A paper book run before its result means anything?

    uv run python research/spike/run_pathA_power.py [--out reports/pathA_power]

Uses the frozen pathA-v1 rules (SMA100 trend x 7-day funding overlay, as in
trading.alerts.risk_check) on development + validation only; the holdout is
never touched. Reports how often the rules act (signals per year, share of
days not fully in BTC) and a block-bootstrap power analysis of the daily
excess return over BTC buy & hold, assuming the historical effect is the true one.

The owner's pass rule is risk-adjusted (net > 0 and Sharpe above buy & hold, or
max drawdown at most half of it), so the main analysis bootstraps that rule:
90-day blocks of (BTC return, weight held) are drawn together for the rules
("power": how often a window of N days passes if history repeats) and drawn
independently for a same-exposure, random-timing control ("false pass": how
often a rule with no timing skill passes anyway).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from common import jsonable, load_config, partitions
from trading.alerts.risk_check import signals
from trading.evaluation.metrics import max_drawdown
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import daily_bars

HORIZONS = [90, 180, 365, 730, 1095, 1825, 3650]
BLOCK = 30
REPS = 4000
ALPHA_Z = 1.645            # one-sided 5%


def boot_means(e: np.ndarray, n: int, rng) -> np.ndarray:
    """Circular block bootstrap of the mean of n days."""
    T = len(e)
    k = -(-n // BLOCK)
    starts = rng.integers(0, T, size=(REPS, k))
    idx = (starts[:, :, None] + np.arange(BLOCK)) % T
    return e[idx.reshape(REPS, -1)[:, :n]].mean(axis=1)


def _sharpe(r: np.ndarray) -> np.ndarray:
    sd = r.std(axis=1)
    return np.where(sd > 0, r.mean(axis=1) / sd * np.sqrt(365), 0.0)


def _max_dd(r: np.ndarray) -> np.ndarray:
    eq = np.cumprod(1 + r, axis=1)
    return (1 - eq / np.maximum.accumulate(eq, axis=1)).max(axis=1)


def passes(rs: np.ndarray, rb: np.ndarray) -> np.ndarray:
    net = np.prod(1 + rs, axis=1) - 1
    return (net > 0) & ((_sharpe(rs) > _sharpe(rb)) | (_max_dd(rs) <= 0.5 * _max_dd(rb)))


def _blocks(T: int, n: int, block: int, rng) -> np.ndarray:
    k = -(-n // block)
    starts = rng.integers(0, T, size=(REPS, k))
    return ((starts[:, :, None] + np.arange(block)) % T).reshape(REPS, -1)[:, :n]


def criterion_power(r_btc: np.ndarray, w: np.ndarray, cost: np.ndarray, n: int, rng, block: int = 90) -> dict:
    T = len(r_btc)
    i = _blocks(T, n, block, rng)
    j = _blocks(T, n, block, rng)                     # independent draw: timing unrelated to returns
    rb = r_btc[i]
    rs = w[i] * rb - cost[i]
    rn = w[j] * rb - cost[j]
    return {"power": float(passes(rs, rb).mean()), "false_pass": float(passes(rn, rb).mean())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--out", default="reports/pathA_power")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    d = Path(cfg["data_dir"])
    h1 = load(d, "BTCUSDT", "1h")
    daily = daily_bars(h1)
    f = load_funding(d, "BTCUSDT")
    sig = signals(daily, f.groupby(f.index.floor("1D")).sum())
    w = sig["suggested_btc_weight"]
    parts = partitions(h1, cfg["partitions"], pd.Timedelta(hours=4 * 600))
    start, end = parts["development"][0], parts["validation"][1]

    eq, trades = simulate_weights(daily, w.fillna(1.0), start, end)
    bh, _ = simulate_weights(daily, pd.Series(1.0, index=daily.index), start, end)
    rs, rb = eq.pct_change().dropna(), bh.pct_change().dropna()
    e = (rs - rb).to_numpy()
    years = len(e) / 365
    held = w.shift(1).reindex(rs.index).fillna(1.0)
    changes = int((held.diff().fillna(0) != 0).sum())

    rng = np.random.default_rng(7)
    centred = e - e.mean()
    power = {}
    for n in HORIZONS:
        null = boot_means(centred, n, rng)
        alt = boot_means(e, n, rng)
        crit = np.quantile(null, 0.95)
        power[n] = {"power_mean_excess": float((alt > crit).mean()),
                    "p_excess_positive": float((alt > 0).mean())}
    r_btc = daily["close"].pct_change().reindex(rs.index).fillna(0).to_numpy()
    wh = held.to_numpy()
    cost = held.diff().abs().fillna(0).to_numpy() * (0.001 + 2e-4)
    criterion = {n: criterion_power(r_btc, wh, cost, n, rng) for n in HORIZONS}
    # analytic n for 80% power, with block-adjusted variance (variance of 30-day block means)
    blk = pd.Series(e).groupby(np.arange(len(e)) // BLOCK).mean()
    var_eff = float(blk.var() * BLOCK)
    n80 = (ALPHA_Z + 0.8416) ** 2 * var_eff / e.mean() ** 2 if e.mean() > 0 else float("inf")

    report = {
        "period": [str(start), str(end)], "holdout_simulated": False, "days": len(e),
        "weight_changes": changes, "weight_changes_per_year": changes / years,
        "trades": int(len(trades)), "share_days_below_100pct": float((held < 1).mean()),
        "share_days_out_of_btc": float((held == 0).mean()),
        "net_return": float(eq.iloc[-1] / eq.iloc[0] - 1), "bh_net_return": float(bh.iloc[-1] / bh.iloc[0] - 1),
        "max_dd": max_drawdown(eq), "bh_max_dd": max_drawdown(bh),
        "excess_mean_daily": float(e.mean()), "excess_std_daily": float(e.std()),
        "excess_annual": float(e.mean() * 365),
        "days_for_80pct_power_analytic": n80, "years_for_80pct_power_analytic": n80 / 365,
        "excess_mean_test_by_horizon_days": power,
        "pass_rule_by_horizon_days": criterion,
        "note": "bootstrap assumes dev+val behaviour is the truth; it is in-sample for these rules",
    }
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
