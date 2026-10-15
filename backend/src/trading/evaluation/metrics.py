"""Report metrics — BUILDPLAN v4.2 §17.1 (subset needed by the research spike)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

ANNUALIZATION_DAYS = 365


def daily_log_returns(daily_equity: pd.Series) -> pd.Series:
    eq = daily_equity.astype(float)
    if (eq <= 0).any():
        raise ValueError("equity <= 0: report ruin instead of log returns (§11.3)")
    return np.log(eq / eq.shift(1)).dropna()


def max_drawdown(series: pd.Series) -> float:
    """Maximum peak-to-trough fraction on the given mark series."""
    s = series.dropna().astype(float)
    if s.empty:
        return float("nan")
    peak = s.cummax()
    return float(((peak - s) / peak).max())


def max_drawdown_intraday(equity_close: pd.Series, equity_worst: pd.Series) -> float:
    """Peak on close marks, trough on worst intrabar marks (conservative)."""
    c = equity_close.dropna().astype(float)
    w = equity_worst.reindex(c.index).fillna(c).astype(float)
    peak = c.cummax()
    return float(((peak - w) / peak).max())


def sharpe(r: pd.Series) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ANNUALIZATION_DAYS)) if sd and sd > 0 else float("nan")


def sortino(r: pd.Series) -> float:
    downside = np.sqrt((np.minimum(r, 0) ** 2).mean())
    return float(r.mean() / downside * math.sqrt(ANNUALIZATION_DAYS)) if downside > 0 else float("nan")


def trade_stats(trades: pd.DataFrame, months: float) -> dict:
    if trades.empty:
        return {"trades": 0, "trades_per_month": 0.0}
    net = trades["net_pnl"]
    wins = net[net > 0]
    losses = net[net <= 0]
    r = trades["r_multiple"].dropna()
    return {
        "trades": int(len(trades)),
        "trades_per_month": float(len(trades) / months) if months > 0 else float("nan"),
        "win_rate": float((net > 0).mean()),
        "expectancy_usdt": float(net.mean()),
        "expectancy_r": float(r.mean()) if len(r) else float("nan"),
        "median_r": float(r.median()) if len(r) else float("nan"),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("nan"),
        "avg_hold_hours": float(trades["hold_hours"].mean()),
        "longs": int((trades["side"] == "LONG").sum()),
        "shorts": int((trades["side"] == "SHORT").sum()),
        "total_fees": float(trades["fees"].sum()),
        "total_funding": float(trades["funding"].sum()),
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
        "ambiguous_bars": int(trades["ambiguous_bar"].sum()),
        "max_consecutive_losses": _max_run(net <= 0),
    }


def _max_run(mask: pd.Series) -> int:
    best = run = 0
    for m in mask:
        run = run + 1 if m else 0
        best = max(best, run)
    return best


def exposure(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, equity: float) -> dict:
    """Time in market and average gross notional / equity while in market."""
    span = (end - start) / pd.Timedelta(hours=1)
    if trades.empty or span <= 0:
        return {"time_in_market": 0.0, "avg_notional_to_equity_in_market": 0.0}
    held = trades["hold_hours"].sum()
    notional = (trades["entry_price"] * trades["qty"] * trades["hold_hours"]).sum() / held if held > 0 else 0.0
    return {
        "time_in_market": float(held / span),
        "avg_notional_to_equity_in_market": float(notional / equity),
    }


def summary(result, *, initial_equity: float, prices: pd.Series) -> dict:
    """Headline metrics for one backtest plus the BTC buy-and-hold proxy."""
    daily = result.daily_equity
    r = daily_log_returns(daily)
    start, end = result.stats["start"], result.stats["end"]
    months = (end - start) / pd.Timedelta(days=30.4375)
    out = {
        "start": str(start), "end": str(end), "days": int(len(r)),
        "final_equity": float(daily.iloc[-1]),
        "net_return": float(daily.iloc[-1] / initial_equity - 1),
        "sharpe_daily_365": sharpe(r),
        "sortino_daily_365": sortino(r),
        "max_drawdown_daily_close": max_drawdown(daily),
    }
    if result.equity_close is not None:
        out["max_drawdown_intraday"] = max_drawdown_intraday(result.equity_close, result.equity_worst)
    dd = out.get("max_drawdown_intraday", out["max_drawdown_daily_close"])
    years = len(r) / ANNUALIZATION_DAYS
    if years > 0 and dd > 0 and out["final_equity"] > 0:
        cagr = (out["final_equity"] / initial_equity) ** (1 / years) - 1
        out["calmar"] = float(cagr / dd)
    out.update(trade_stats(result.trades, months))
    out.update(exposure(result.trades, start, end, initial_equity))
    out["breaches"] = [{"time": str(e["time"]), "event": e["event"]} for e in result.events]
    out.update(buy_and_hold(prices, start, end, initial_equity))
    return out


def buy_and_hold(prices: pd.Series, start, end, initial_equity: float, fee: float = 0.0005) -> dict:
    """BTC buy-and-hold proxy on the perp close series (spot venue not yet chosen)."""
    p = prices.loc[start:end].dropna()
    if p.empty:
        return {}
    daily = p.resample("1D").last().dropna()
    eq = initial_equity * (1 - fee) * daily / p.iloc[0]
    r = np.log(eq / eq.shift(1)).dropna()
    return {
        "bh_net_return": float(eq.iloc[-1] / initial_equity - 1),
        "bh_sharpe_daily_365": sharpe(r),
        "bh_max_drawdown_daily_close": max_drawdown(eq),
        "bh_source": "BTCUSDT perpetual close as spot proxy",
    }
