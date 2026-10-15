"""trend_regime_v2 — pre-declared in docs/research/v2-plan.md.

Daily trend regime: hold the coin (1x, long only) while the daily close is
above its SMA, otherwise hold cash. Decisions use closed daily bars only and
execute at the next day's open.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.strategies.trend_breakout_v1 import regularize_h1

STRATEGY_ID = "trend_regime_v2"


@dataclass(frozen=True)
class RegimeCosts:
    fee: float = 0.0010        # spot taker, per side
    slip_bps: float = 2.0


def daily_bars(h1_raw: pd.DataFrame) -> pd.DataFrame:
    """UTC daily bars from 1h bars; a day is valid only with all 24 hours valid."""
    h1 = regularize_h1(h1_raw)
    g = h1.groupby(h1.index.floor("1D"))
    d = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last(),
                      "n_valid": g["valid"].sum()})
    d["valid"] = d["n_valid"] == 24
    d.loc[~d["valid"], ["open", "close"]] = np.nan
    return d.drop(columns="n_valid")


def regime_state(daily: pd.DataFrame, sma_n: int = 100) -> pd.Series:
    """True (IN) when the close is above its SMA at that day's close. Invalid
    days and days before the SMA exists keep the previous state (start: OUT)."""
    sma = daily["close"].rolling(sma_n, min_periods=sma_n).mean()
    raw = pd.Series(np.where(sma.notna() & daily["close"].notna(), daily["close"] > sma, np.nan),
                    index=daily.index, dtype=object)
    return raw.ffill().fillna(False).astype(bool)


def simulate(daily: pd.DataFrame, state: pd.Series, start, end, *, equity: float = 10_000.0,
             costs: RegimeCosts = RegimeCosts(), funding_daily: pd.Series | None = None):
    """Run days in [start, end). The position held during day d is the state at
    the close of day d-1, traded at day d's open. Returns (daily close equity
    with a starting point the day before ``start``, trade list)."""
    days = daily.loc[(daily.index >= pd.Timestamp(start)) & (daily.index < pd.Timestamp(end))]
    target = state.shift(1).fillna(False).astype(bool).reindex(days.index)
    cash, qty = equity, 0.0
    marks, trades = [], []
    last_px = np.nan
    for d, row in days.iterrows():
        o, c = row["open"], row["close"]
        want = bool(target.loc[d])
        if np.isfinite(o):
            if want and qty == 0.0:
                px = o * (1 + costs.slip_bps / 1e4)
                qty = cash * (1 - costs.fee) / px
                trades.append({"time": d, "side": "BUY", "price": px, "qty": qty, "equity": cash})
                cash = 0.0
            elif not want and qty > 0.0:
                px = o * (1 - costs.slip_bps / 1e4)
                cash = qty * px * (1 - costs.fee)
                trades.append({"time": d, "side": "SELL", "price": px, "qty": qty, "equity": cash})
                qty = 0.0
        if np.isfinite(c):
            last_px = c
        if qty > 0.0 and funding_daily is not None and d in funding_daily.index and np.isfinite(last_px):
            cash -= qty * last_px * float(funding_daily.loc[d])
        marks.append(cash + qty * (last_px if np.isfinite(last_px) else 0.0))
    idx = days.index
    eq = pd.Series(marks, index=idx, name="equity")
    eq = pd.concat([pd.Series([equity], index=[idx[0] - pd.Timedelta(days=1)]), eq])
    return eq, pd.DataFrame(trades)
