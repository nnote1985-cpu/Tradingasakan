"""1h bars → 1h/4h/1d bars, and indicators the library needs beyond trading.features.indicators.

Bars are indexed by their open time (UTC). A 4h or 1d bar is valid only when every
1h bar inside it is valid; invalid bars have NaN prices and are never forward-filled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading.features.indicators import ema
from trading.strategies.trend_breakout_v1 import regularize_h1

TIMEFRAMES: dict[str, pd.Timedelta] = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}


def resample(h1_raw: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; allowed: {sorted(TIMEFRAMES)}")
    h1 = regularize_h1(h1_raw)
    if timeframe == "1h":
        return h1[["open", "high", "low", "close", "valid"]]
    step = TIMEFRAMES[timeframe]
    g = h1.groupby(pd.DatetimeIndex(h1.index).floor(step))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "n_valid": g["valid"].sum()})
    out["valid"] = out["n_valid"] == int(step / pd.Timedelta(hours=1))
    out.loc[~out["valid"], ["open", "high", "low", "close"]] = np.nan
    out.index.name = "open_time"
    return out.drop(columns="n_valid")


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def macd(close: np.ndarray, fast: int, slow: int, signal: int) -> tuple[np.ndarray, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    sig = np.full(line.shape, np.nan)
    ok = ~np.isnan(line)
    if ok.any():
        sig[ok] = ema(line[ok], signal)
    return line, sig


def rsi_wilder(close: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI: first average is the SMA of the first ``n`` changes, then (prev*(n-1)+x)/n."""
    close = np.asarray(close, dtype=float)
    out = np.full(close.shape, np.nan)
    if len(close) <= n:
        return out
    d = np.diff(close)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(close)):
        if i > n:
            au = (au * (n - 1) + up[i - 1]) / n
            ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out
