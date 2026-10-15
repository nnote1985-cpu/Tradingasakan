"""trend_donchian_4h_v1.1 — pre-declared in docs/research/v1.1-plan.md.

4h Donchian breakout in the direction of the 4h EMA50/EMA200 trend, no fixed
take profit, exit on the opposite (shorter) Donchian channel. Pure strategy:
closed, validated bars only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.features.indicators import atr_wilder, ema, previous_high, previous_low
from trading.strategies.trend_breakout_v1 import H4, _contiguous_count, build_h4, regularize_h1

STRATEGY_ID = "trend_donchian_4h_v1.1"


@dataclass(frozen=True)
class DonchianParams:
    ema_fast_4h: int = 50
    ema_slow_4h: int = 200
    atr_n: int = 14
    entry_lookback: int = 20
    exit_lookback: int = 10
    warmup_4h_bars: int = 600


def build_features(h1_raw: pd.DataFrame, params: DonchianParams = DonchianParams()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (signal frame, h4 frame), both indexed by 4h open time.

    The signal frame has the columns the simulator's candidate builder reads
    (``signal``, ``close_time``, ``atr``). The h4 frame carries ``ema_slow`` for
    the trend exit and ``long_exit``/``short_exit`` flags known at ``close_time``.
    """
    p = params
    h4 = build_h4(regularize_h1(h1_raw))
    hi, lo, cl = (h4[c].to_numpy(float) for c in ("high", "low", "close"))
    h4["ema_fast"] = ema(cl, p.ema_fast_4h)
    h4["ema_slow"] = ema(cl, p.ema_slow_4h)
    h4["atr"] = atr_wilder(hi, lo, cl, p.atr_n)
    h4["entry_high"] = previous_high(hi, p.entry_lookback)
    h4["entry_low"] = previous_low(lo, p.entry_lookback)
    h4["exit_low"] = previous_low(lo, p.exit_lookback)
    h4["exit_high"] = previous_high(hi, p.exit_lookback)
    h4["run"] = _contiguous_count(h4["valid"].to_numpy())

    with np.errstate(invalid="ignore"):
        h4["long_exit"] = h4["valid"] & (h4["close"] < h4["exit_low"])
        h4["short_exit"] = h4["valid"] & (h4["close"] > h4["exit_high"])
    h4["signal"] = _signal(h4, p)
    return h4[["signal", "close_time", "atr"]].copy(), h4


def _signal(h4: pd.DataFrame, p: DonchianParams) -> np.ndarray:
    c = h4["close"].to_numpy(float)
    ef, es = h4["ema_fast"].to_numpy(float), h4["ema_slow"].to_numpy(float)
    eh, el = h4["entry_high"].to_numpy(float), h4["entry_low"].to_numpy(float)
    atr = h4["atr"].to_numpy(float)
    finite = np.isfinite(np.vstack([c, ef, es, eh, el, atr])).all(axis=0)
    ok = h4["valid"].to_numpy(bool) & (h4["run"].to_numpy() >= p.warmup_4h_bars) & finite
    with np.errstate(invalid="ignore"):
        ok &= atr > 0
        long_ok = ok & (ef > es) & (c > es) & (c > eh)
        short_ok = ok & (ef < es) & (c < es) & (c < el)
    sig = np.full(len(h4), "NO_TRADE", dtype=object)
    sig[long_ok & ~short_ok] = "LONG"
    sig[short_ok & ~long_ok] = "SHORT"
    return sig


__all__ = ["STRATEGY_ID", "DonchianParams", "build_features", "H4"]
