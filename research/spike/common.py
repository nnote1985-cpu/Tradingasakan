"""Shared helpers for the Phase 0.5 research spike scripts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from trading.market_data.binance_public import load, load_funding
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, ExitParams
from trading.strategies.trend_breakout_v1 import StrategyParams, build_features


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def load_market(cfg: dict) -> dict:
    """1h bars (signals), execution bars (1m if present, else 1h) and funding."""
    d = Path(cfg["data_dir"])
    h1 = load(d, cfg["symbol"], "1h")
    exec_interval = cfg.get("exec_interval", "1m")
    p = d / f"{cfg['symbol']}_{exec_interval}.parquet"
    if exec_interval != "1h" and p.exists():
        ex = load(d, cfg["symbol"], exec_interval)
    else:
        exec_interval = "1h"
        ex = h1
    ex = regular_grid(ex, pd.Timedelta(exec_interval))
    funding = load_funding(d, cfg["symbol"])
    return {"h1": h1, "exec": ex, "exec_interval": exec_interval, "funding": funding}


def regular_grid(bars: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    """Missing execution bars stay NaN so the simulator can count them (§4.2)."""
    idx = pd.date_range(bars.index[0], bars.index[-1], freq=step, tz="UTC")
    return bars[["open", "high", "low", "close"]].reindex(idx)


def partitions(h1: pd.DataFrame, fractions: dict, warmup: pd.Timedelta) -> dict:
    """Chronological split of the post-warmup span. The holdout boundary is
    recorded but never run by these scripts (BUILDPLAN v4.2 §17.2)."""
    start = h1.index[0] + warmup
    end = h1.index[-1] + pd.Timedelta(hours=1)
    span = end - start
    dev_end = (start + span * fractions["development"]).normalize()
    val_end = (start + span * (fractions["development"] + fractions["validation"])).normalize()
    return {
        "development": (start.normalize() + pd.Timedelta(days=1), dev_end),
        "validation": (dev_end, val_end),
        "holdout": (val_end, end),
    }


def make_backtester(market: dict, *, params: StrategyParams = StrategyParams(),
                    config: BacktestConfig | None = None, features=None) -> BarBacktester:
    h1, h4 = features if features is not None else build_features(market["h1"], params)
    return BarBacktester(market["exec"], h1, h4, market["funding"], config or BacktestConfig())


def with_exits(config: BacktestConfig, *, stop_atr_mult=None, take_profit_r=None,
               time_exit_hours=None) -> BacktestConfig:
    e = config.exits
    return replace(config, exits=ExitParams(
        stop_atr_mult=stop_atr_mult if stop_atr_mult is not None else e.stop_atr_mult,
        take_profit_r=take_profit_r if take_profit_r is not None else e.take_profit_r,
        time_exit=pd.Timedelta(hours=time_exit_hours) if time_exit_hours is not None else e.time_exit,
        cooldown=e.cooldown,
    ))


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, pd.Timedelta)):
        return str(x)
    return x
