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
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from trading.features.indicators import atr_wilder, ema
from trading.strategies.library.bars import TIMEFRAMES, macd, rsi_wilder, sma

Params = dict[str, Any]  # values checked by Param.check
WEIGHTS = (0.0, 0.5, 1.0)


class OHLC(NamedTuple):
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray


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
    rule: Callable[[OHLC, Params], np.ndarray] = field(repr=False)
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
        w_valid = self.rule(OHLC(*(bars[k].to_numpy(dtype=float)[ok] for k in ("open", "high", "low", "close"))), p)
        w = pd.Series(np.nan, index=bars.index)
        w[ok] = w_valid
        w = w.ffill().fillna(0.0)
        if not w.isin(WEIGHTS).all():
            raise AssertionError(f"strategy {self.no} produced a weight outside {WEIGHTS}")
        w.index = bars.index + TIMEFRAMES[tf]
        w.index.name = "close_time"
        return w.rename(f"s{self.no}")


# ---- rules: (OHLC of valid bars, params) → weights; NaN indicator → 0 ----------------

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


def _macd_cross(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    with np.errstate(invalid="ignore"):
        return np.where(line > sig, 1.0, 0.0)


def _macd_divergence(b: OHLC, p: Params) -> np.ndarray:
    """Enter on a MACD cross up when, over the last ``lookback`` bars, price made a lower
    low than the ``lookback`` bars before while the MACD line made a higher low. Exit on a
    MACD cross down."""
    c, lo = b.c, b.l
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    n = p["lookback"]
    lo_s, ln_s = pd.Series(lo), pd.Series(line)
    p_now, p_before = lo_s.rolling(n, min_periods=n).min(), lo_s.shift(n).rolling(n, min_periods=n).min()
    m_now, m_before = ln_s.rolling(n, min_periods=n).min(), ln_s.shift(n).rolling(n, min_periods=n).min()
    div = ((p_now < p_before) & (m_now > m_before)).to_numpy()
    return _latch(div & _cross_up(line, sig), _cross_down(line, sig))


def _rsi_ob_os(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    r = rsi_wilder(c, p["period"])
    with np.errstate(invalid="ignore"):
        return _latch(r < p["oversold"], r > p["overbought"])


def _rsi_reentry(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    r = rsi_wilder(c, p["period"])
    os_, ob = np.full(len(r), float(p["oversold"])), np.full(len(r), float(p["overbought"]))
    return _latch(_cross_up(r, os_), _cross_down(r, ob))


def _ema_macd_trend(b: OHLC, p: Params) -> np.ndarray:
    """1 above the trend EMA with MACD above its signal, 0.5 above the EMA otherwise, 0 below."""
    c = b.c
    e = ema(c, p["trend_ema"])
    line, sig = macd(c, p["fast"], p["slow"], p["signal"])
    with np.errstate(invalid="ignore"):
        up = c > e
        return np.where(up & (line > sig), 1.0, np.where(up, 0.5, 0.0))


def _momentum(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    n = p["lookback"]
    roc = np.full(len(c), np.nan)
    roc[n:] = c[n:] / c[:-n] - 1.0
    with np.errstate(invalid="ignore"):
        return np.where(roc > p["threshold"], 1.0, 0.0)


def _sma_regime(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    s = sma(c, p["sma"])
    with np.errstate(invalid="ignore"):
        return np.where(c > s, 1.0, 0.0)


def _donchian(b: OHLC, p: Params) -> np.ndarray:
    c = b.c
    cs = pd.Series(c)
    hi_prev = cs.shift(1).rolling(p["entry"], min_periods=p["entry"]).max().to_numpy()
    lo_prev = cs.shift(1).rolling(p["exit"], min_periods=p["exit"]).min().to_numpy()
    with np.errstate(invalid="ignore"):
        return _latch(c > hi_prev, c < lo_prev)


# ---- #9–#13: long side of jesse-ai/example-strategies (MIT, (c) 2020 jesse-ai) -------------
# Rewritten for closed bars and {0, 0.5, 1} weights. Stops, ATR position sizing and shorts in
# the originals are left out (our books size by weight and spot.v1 handles losses); each rule
# says where it differs from the original.

def _rsi2(b: OHLC, p: Params) -> np.ndarray:
    """Connors RSI(2): buy above the slow SMA when RSI is at or below oversold; sell on a
    close above the fast SMA (jesse RSI2)."""
    r = rsi_wilder(b.c, p["rsi_period"])
    fast, slow = sma(b.c, p["fast_sma"]), sma(b.c, p["slow_sma"])
    with np.errstate(invalid="ignore"):
        return _latch((b.c > slow) & (r <= p["oversold"]), b.c > fast)


def _bollinger(b: OHLC, p: Params) -> np.ndarray:
    """Buy on a close above the upper Bollinger band (source (high+low)/2) while the close is
    above the Ichimoku cloud; sell on a close below the middle band (jesse SimpleBollinger)."""
    hl2 = pd.Series((b.h + b.l) / 2)
    mid = hl2.rolling(p["period"], min_periods=p["period"]).mean()
    upper = (mid + p["mult"] * hl2.rolling(p["period"], min_periods=p["period"]).std(ddof=0)).to_numpy()
    hs, ls, d = pd.Series(b.h), pd.Series(b.l), p["displacement"]

    def mid_range(n: int) -> pd.Series:
        return (hs.rolling(n, min_periods=n).max() + ls.rolling(n, min_periods=n).min()) / 2

    span_a = ((mid_range(p["conversion"]) + mid_range(p["base"])) / 2).shift(d).to_numpy()
    span_b = mid_range(p["lagging"]).shift(d).to_numpy()
    with np.errstate(invalid="ignore"):
        return _latch((b.c > upper) & (b.c > span_a) & (b.c > span_b), b.c < mid.to_numpy())


def _dual_thrust(b: OHLC, p: Params) -> np.ndarray:
    """Range = max(highest close - lowest low, highest high - lowest close) over the previous
    ``length`` bars. Buy on a close above anchor + up_coeff*range; sell on a close below
    anchor - down_coeff*range, where anchor is the open ``anchor_bars`` bars back (counting this
    bar as 1). The original anchors on the open of the current higher-timeframe candle (4h → 1D)
    and counts the current bar in the range."""
    n, a = p["length"], p["anchor_bars"]
    anchor = pd.Series(b.o).shift(a - 1).to_numpy()
    h, lo, c = (pd.Series(x).shift(1) for x in (b.h, b.l, b.c))
    hh, ll = h.rolling(n, min_periods=n).max(), lo.rolling(n, min_periods=n).min()
    hc, lc = c.rolling(n, min_periods=n).max(), c.rolling(n, min_periods=n).min()
    rng = np.maximum((hc - ll).to_numpy(), (hh - lc).to_numpy())
    with np.errstate(invalid="ignore"):
        return _latch(b.c > anchor + p["up_coeff"] * rng, b.c < anchor - p["down_coeff"] * rng)


def _kdj(b: OHLC, p: Params) -> np.ndarray:
    """KDJ (K = SMA of fast stochastic, D = SMA of K, J = 3K - 2D). Buy when J is above K and
    D; sell when J is below both, or when J turns down while the close is above the entry
    close (jesse KDJstrategy, without its 2xATR stop)."""
    n = p["period"]
    hh = pd.Series(b.h).rolling(n, min_periods=n).max()
    ll = pd.Series(b.l).rolling(n, min_periods=n).min()
    fastk = 100 * (pd.Series(b.c) - ll) / (hh - ll).replace(0, np.nan)
    k = fastk.rolling(p["k_smooth"], min_periods=p["k_smooth"]).mean()
    d = k.rolling(p["d_smooth"], min_periods=p["d_smooth"]).mean()
    kk, dd = k.to_numpy(), d.to_numpy()
    j = 3 * kk - 2 * dd
    out = np.zeros(len(j))
    held, entry = False, 0.0
    for i in range(len(j)):
        if np.isnan(j[i]):
            out[i] = 1.0 if held else 0.0
            continue
        if held:
            turned_down = i > 0 and not np.isnan(j[i - 1]) and j[i] < j[i - 1]
            if (b.c[i] > entry and turned_down) or (j[i] < kk[i] and j[i] < dd[i]):
                held = False
        elif j[i] > kk[i] and j[i] > dd[i]:
            held, entry = True, b.c[i]
        out[i] = 1.0 if held else 0.0
    return out


def _turtle(b: OHLC, p: Params) -> np.ndarray:
    """Turtle System 1, long side: 0.5 when the high breaks the previous ``entry``-bar high;
    1 once the close is ``add_atr`` ATRs above the entry close; 0 when the low breaks the
    previous ``exit``-bar low. The original adds up to 4 units with ATR sizing and stops."""
    hi_prev = pd.Series(b.h).shift(1).rolling(p["entry"], min_periods=p["entry"]).max().to_numpy()
    lo_prev = pd.Series(b.l).shift(1).rolling(p["exit"], min_periods=p["exit"]).min().to_numpy()
    atr = atr_wilder(b.h, b.l, b.c, p["atr_period"])
    out = np.zeros(len(b.c))
    w, entry = 0.0, 0.0
    for i in range(len(b.c)):
        if w > 0 and lo_prev[i] == lo_prev[i] and b.l[i] < lo_prev[i]:
            w = 0.0
        elif w == 0 and hi_prev[i] == hi_prev[i] and b.h[i] > hi_prev[i]:
            w, entry = 0.5, b.c[i]
        elif w == 0.5 and atr[i] == atr[i] and b.c[i] > entry + p["add_atr"] * atr[i]:
            w = 1.0
        out[i] = w
    return out


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
    StrategySpec(9, "rsi2_connors", "1.0", "RSI2 (Connors)", "mean_reversion", _ALL_TF, "1d",
                 {"rsi_period": Param(2, 2, 5), "oversold": Param(10, 1, 30), "fast_sma": Param(5, 3, 20), "slow_sma": Param(200, 50, 300)},
                 _rsi2, "Buy above the 200 SMA when RSI(2) is 10 or lower; sell on a close above the 5 SMA. From jesse-ai RSI2."),
    StrategySpec(10, "bollinger_breakout", "1.0", "Bollinger Bands breakout", "trend", _ALL_TF, "4h",
                 {"period": Param(20, 10, 50), "mult": Param(2.0, 1.0, 3.0), "conversion": Param(9, 5, 20),
                  "base": Param(26, 10, 60), "lagging": Param(52, 20, 120), "displacement": Param(26, 10, 60)},
                 _bollinger,
                 "Buy on a close above the upper band while above the Ichimoku cloud; sell below the middle band. From jesse-ai SimpleBollinger."),
    StrategySpec(11, "dual_thrust", "1.0", "Dual Thrust", "trend", _ALL_TF, "4h",
                 {"length": Param(21, 3, 60), "anchor_bars": Param(6, 1, 30), "up_coeff": Param(0.71, 0.1, 3.0), "down_coeff": Param(0.67, 0.1, 3.0)},
                 _dual_thrust,
                 "Buy when the close clears the open of N bars back by a share of the recent range; sell when it falls below. "
                 "From jesse-ai DUAL_THRUST."),
    StrategySpec(12, "kdj", "1.0", "KDJ", "momentum", _ALL_TF, "1d",
                 {"period": Param(9, 5, 30), "k_smooth": Param(3, 1, 10), "d_smooth": Param(3, 1, 10)},
                 _kdj, "Buy when J is above K and D; sell when J drops below both or turns down in profit. From jesse-ai KDJstrategy."),
    StrategySpec(13, "turtle_s1", "1.0", "Turtle Rules (System 1)", "trend", _ALL_TF, "1d",
                 {"entry": Param(20, 10, 100), "exit": Param(10, 5, 50), "atr_period": Param(20, 5, 50), "add_atr": Param(0.5, 0.1, 3.0)},
                 _turtle, "Half position on a 20-bar high breakout, full after +0.5 ATR, out on a 10-bar low. From jesse-ai TurtleRules."),
]}


def get(no: int) -> StrategySpec:
    if no not in CATALOG:
        raise ValueError(f"no strategy number {no}; known: {sorted(CATALOG)}")
    return CATALOG[no]
