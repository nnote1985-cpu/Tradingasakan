"""funding_overlay_v3 — pre-declared in docs/research/v3-funding-overlay-plan.md.

BTC core position; exposure is cut while trailing perpetual funding is elevated.
Decisions use data known at the daily close and trade at the next day's open.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.strategies.trend_regime_v2 import RegimeCosts

STRATEGY_ID = "funding_overlay_v3"


@dataclass(frozen=True)
class OverlayParams:
    window_days: int = 7
    threshold_per_day: float = 0.0009   # 0.03% per 8h
    elevated_weight: float = 0.5


def funding_weights(funding_daily: pd.Series, days: pd.DatetimeIndex,
                    p: OverlayParams = OverlayParams()) -> pd.Series:
    """Target weight decided at each day's close (index = that day)."""
    f = funding_daily.reindex(days)
    mean = f.rolling(p.window_days, min_periods=p.window_days).mean()
    elevated = mean > p.threshold_per_day
    return pd.Series(np.where(elevated, p.elevated_weight, 1.0), index=days)


def simulate_weights(daily: pd.DataFrame, weight: pd.Series, start, end, *,
                     equity: float = 10_000.0, costs: RegimeCosts = RegimeCosts()):
    """Hold ``weight`` (decided at day d-1 close) of equity in the coin during
    day d, rebalancing only the difference at day d's open."""
    days = daily.loc[(daily.index >= pd.Timestamp(start)) & (daily.index < pd.Timestamp(end))]
    target = weight.shift(1).reindex(days.index).fillna(1.0)
    cash, qty = equity, 0.0
    marks, trades = [], []
    last_px = np.nan
    for d, row in days.iterrows():
        o, c = row["open"], row["close"]
        if np.isfinite(o):
            eq_open = cash + qty * o
            delta_value = float(target.loc[d]) * eq_open - qty * o
            if abs(delta_value) > 1e-9 * eq_open:
                if delta_value > 0:
                    px = o * (1 + costs.slip_bps / 1e4)
                    spend = min(delta_value, cash)
                    dq = spend * (1 - costs.fee) / px
                    cash -= spend
                    qty += dq
                    trades.append({"time": d, "side": "BUY", "price": px, "qty": dq})
                else:
                    px = o * (1 - costs.slip_bps / 1e4)
                    dq = min(-delta_value / o, qty)
                    cash += dq * px * (1 - costs.fee)
                    qty -= dq
                    trades.append({"time": d, "side": "SELL", "price": px, "qty": dq})
        if np.isfinite(c):
            last_px = c
        marks.append(cash + qty * (last_px if np.isfinite(last_px) else 0.0))
    idx = days.index
    eq = pd.Series(marks, index=idx, name="equity")
    eq = pd.concat([pd.Series([equity], index=[idx[0] - pd.Timedelta(days=1)]), eq])
    return eq, pd.DataFrame(trades)
