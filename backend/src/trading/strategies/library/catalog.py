"""The numbered strategy catalog.

Each entry is a ``StrategySpec``: a fixed number (never reused), a template id with
a version, the timeframes it may run on, and its parameters with defaults and
bounds. ``spec.signal(bars, params)`` maps closed bars (``bars.resample``) to a
target weight per bar, indexed by the bar's close time.

Signals are computed on valid bars only (invalid bars are skipped, not filled) and
the weight holds through invalid bars. Before an indicator exists the weight is 0.
A signal at a bar uses that bar and earlier bars only.

Adding a strategy: append a new number. Changing a rule: add a new number or bump
``version``; never change what an existing number+version does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trading.features.indicators import ema
from trading.strategies.library.bars import TIMEFRAMES, macd, rsi_wilder, sma

Params = dict[str, Any]  # values checked by Param.check
WEIGHTS = (0.0, 0.5, 1.0)


@dataclass(frozen=True)
class Param:
    default: int | float
    min: int | float
    max: int | float

    def check(self, name: str, value: int | float) -> None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"param {name} must be a number")
        if isinstance(self.default, int) and not isinstance(value, int):
            raise ValueError(f"param {name} must be an integer")
        if not self.min <= value <= self.max:
            raise ValueError(f"param {name}={value} is outside [{self.min}, {self.max}]")


@dataclass(frozen=True)
class StrategySpec:
    no: int
    template_id: str
    version: str
    name: str
    family: str                      # trend | momentum | mean_reversion
    timeframes: tuple[str, ...]
    default_timeframe: str
    params: dict[str, Param]
    rule: Callable[[np.ndarray, np.ndarray, Params], np.ndarray] = field(repr=False)
    summary: str = ""

    def resolve(self, params: Params | None) -> Params:
        params = dict(params or {})
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f"strategy {self.no} ({self.template_id}) has no params {sorted(unknown)}")
        for k, v in params.items():
            self.params[k].check(k, v)
        return {k: params.get(k, p.default) for k, p in self.params.items()}

    def signal(self, bars: pd.DataFrame, params: Params | None = None, timeframe: str | None = None) -> pd.Series:
        """Target weight per bar, indexed by bar close time (open + timeframe)."""
        tf = timeframe or self.default_timeframe
        if tf not in self.timeframes:
            raise ValueError(f"strategy {self.no} does not run on {tf}; allowed {self.timeframes}")
        p = self.resolve(params)
        ok = bars["valid"].to_numpy(dtype=bool)
        w_valid = self.rule(bars["close"].to_numpy(dtype=float)[ok], bars["low"].to_numpy(dtype=float)[ok], p)
        w = pd.Series(np.nan, index=bars.index)
        w[ok] = w_valid
        w = w.ffill().fillna(0.0)
        if not w.isin(WEIGHTS).all():
            raise AssertionError(f"strategy {self.no} produced a weight outside {WEIGHTS}")
        w.index = bars.index + TIMEFRAMES[tf]
        w.index.name = "close_time"
        return w.rename(f"s{self.no}")


# ---- rules: (closes, lows, params) of valid bars → weights; NaN indicator → 0 ----------

def _latch(enter: np.ndarray, exit_: np.ndarray) -> np.ndarray:
    """Flat until ``enter``; long until ``exit_`` (exit wins on the same bar)."""
    out = np.zeros(len(enter))
    held = False
    for i in range(len(enter)):
        if held and exit_[i]:
            held = False
        elif not held and enter[i]:
            held = True
        out[i] = 1.0 if held else 0.0
    return out


def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    prev = np.r_[np.nan, a[:-1] - b[:-1]]
    with np.errstate(invalid="ignore"):
        out: np.ndarray = (a - b > 0) & (prev <= 0)
    return out


def _cross_down(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    prev = np.r_[np.nan, a[:-1] - b[:-1]]
    with np.errstate(invalid="ignore"):
        out: np.ndarray = (a - b < 0) & (prev >= 0)
    return out


def _macd_cross(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    with np.errstate(invalid="ignore"):
        return np.where(line > sig, 1.0, 0.0)


def _macd_divergence(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    """Enter on a MACD cross up when, over the last ``lookback`` bars, price made a lower
    low than the ``lookback`` bars before while the MACD line made a higher low. Exit on a
    MACD cross down."""
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    n = p["lookback"]
    lo_s, ln_s = pd.Series(lo), pd.Series(line)
    p_now, p_before = lo_s.rolling(n, min_periods=n).min(), lo_s.shift(n).rolling(n, min_periods=n).min()
    m_now, m_before = ln_s.rolling(n, min_periods=n).min(), ln_s.shift(n).rolling(n, min_periods=n).min()
    div = ((p_now < p_before) & (m_now > m_before)).to_numpy()
    return _latch(div & _cross_up(line, sig), _cross_down(line, sig))


def _rsi_ob_os(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    r = rsi_wilder(c, p["period"])
    with np.errstate(invalid="ignore"):
        return _latch(r < p["oversold"], r > p["overbought"])


def _rsi_reentry(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    r = rsi_wilder(c, p["period"])
    os_, ob = np.full(len(r), float(p["oversold"])), np.full(len(r), float(p["overbought"]))
    return _latch(_cross_up(r, os_), _cross_down(r, ob))


def _ema_macd_trend(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    """1 above the trend EMA with MACD above its signal, 0.5 above the EMA otherwise, 0 below."""
    e = ema(c, p["trend_ema"])
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    with np.errstate(invalid="ignore"):
        up = c > e
        return np.where(up & (line > sig), 1.0, np.where(up, 0.5, 0.0))


def _momentum(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    n = p["lookback"]
    roc = np.full(len(c), np.nan)
    roc[n:] = c[n:] / c[:-n] - 1.0
    with np.errstate(invalid="ignore"):
        return np.where(roc > p["threshold"], 1.0, 0.0)


def _sma_regime(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    s = sma(c, p["sma"])
    with np.errstate(invalid="ignore"):
        return np.where(c > s, 1.0, 0.0)


def _donchian(c: np.ndarray, lo: np.ndarray, p: Params) -> np.ndarray:
    cs = pd.Series(c)
    hi_prev = cs.shift(1).rolling(p["entry"], min_periods=p["entry"]).max().to_numpy()
    lo_prev = cs.shift(1).rolling(p["exit"], min_periods=p["exit"]).min().to_numpy()
    with np.errstate(invalid="ignore"):
        return _latch(c > hi_prev, c < lo_prev)


_MACD = {"fast": Param(12, 5, 20), "slow": Param(26, 20, 60), "signal": Param(9, 5, 15)}
_ALL_TF = ("1h", "4h", "1d")

CATALOG: dict[int, StrategySpec] = {s.no: s for s in [
    StrategySpec(1, "macd_cross", "1.0", "MACD Crossover", "trend", _ALL_TF, "4h", dict(_MACD), _macd_cross,
                 "Long while the MACD line is above its signal line."),
    StrategySpec(2, "macd_divergence", "1.0", "MACD Divergence", "mean_reversion", _ALL_TF, "4h",
                 {**_MACD, "lookback": Param(14, 5, 50)}, _macd_divergence,
                 "Enter on a MACD cross up after a bullish divergence (price lower low, MACD higher low); exit on a MACD cross down."),
    StrategySpec(3, "rsi_ob_os", "1.0", "RSI Overbought/Oversold", "mean_reversion", _ALL_TF, "4h",
                 {"period": Param(14, 5, 30), "oversold": Param(30, 10, 45), "overbought": Param(70, 55, 90)}, _rsi_ob_os,
                 "Buy when RSI is below oversold; sell when RSI is above overbought."),
    StrategySpec(4, "rsi_reentry", "1.0", "RSI Re-entry", "mean_reversion", _ALL_TF, "4h",
                 {"period": Param(14, 5, 30), "oversold": Param(30, 10, 45), "overbought": Param(70, 55, 90)}, _rsi_reentry,
                 "Buy when RSI crosses back above oversold; sell when it crosses back below overbought."),
    StrategySpec(5, "ema_macd_trend", "1.0", "EMA + MACD Trend Following", "trend", _ALL_TF, "1d",
                 {"trend_ema": Param(200, 20, 300), **_MACD}, _ema_macd_trend,
                 "1 above the trend EMA with MACD above signal, 0.5 above the EMA otherwise, 0 below the EMA."),
    StrategySpec(6, "momentum_roc", "1.0", "Momentum (rate of change)", "momentum", _ALL_TF, "1d",
                 {"lookback": Param(20, 5, 200), "threshold": Param(0.0, -0.2, 0.5)}, _momentum,
                 "Long while the return over the last N bars is above the threshold."),
    StrategySpec(7, "sma_regime", "1.0", "SMA trend regime", "trend", _ALL_TF, "1d",
                 {"sma": Param(100, 10, 300)}, _sma_regime,
                 "Long while the close is above its SMA (the trend rule of research R3 / path A)."),
    StrategySpec(8, "donchian_breakout", "1.0", "Donchian breakout", "trend", _ALL_TF, "4h",
                 {"entry": Param(20, 5, 100), "exit": Param(10, 3, 100)}, _donchian,
                 "Buy when the close breaks the prior N-bar high close; sell below the prior M-bar low close."),
]}


def get(no: int) -> StrategySpec:
    if no not in CATALOG:
        raise ValueError(f"no strategy number {no}; known: {sorted(CATALOG)}")
    return CATALOG[no]
