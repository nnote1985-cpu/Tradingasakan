"""Deterministic indicators pinned by BUILDPLAN v4.2 §6.1.

All functions take a 1-D float array of *closed, validated* bars and return an
array of the same length. Positions before the indicator is defined are NaN.
"""

from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, n: int) -> np.ndarray:
    """EMA seeded with the SMA of the first ``n`` values, then alpha = 2/(n+1).

    ``out[n-1]`` is the seed. A NaN in the input restarts the series after the
    gap; callers that need a continuous checkpoint must not pass gapped data.
    """
    values = np.asarray(values, dtype=float)
    out = np.full(values.shape, np.nan)
    if n <= 0:
        raise ValueError("n must be positive")
    alpha = 2.0 / (n + 1)
    run_start = 0
    i = 0
    length = len(values)
    while i < length:
        if np.isnan(values[i]):
            run_start = i + 1
            i += 1
            continue
        if i - run_start + 1 < n:
            i += 1
            continue
        if i - run_start + 1 == n:
            out[i] = values[run_start : i + 1].mean()
        else:
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
        i += 1
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """TR = max(H-L, |H-prevClose|, |L-prevClose|). The first bar has no TR."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    tr[0] = np.nan
    return tr


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """ATR seeded with the SMA of the first ``n`` TR values, then Wilder smoothing
    ``(prevATR*(n-1) + TR) / n``."""
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    run_start = 1
    for i in range(1, len(tr)):
        if np.isnan(tr[i]):
            run_start = i + 1
            continue
        count = i - run_start + 1
        if count < n:
            continue
        if count == n:
            out[i] = tr[run_start : i + 1].mean()
        else:
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def previous_high(high: np.ndarray, lookback: int = 20) -> np.ndarray:
    """max(high) of the ``lookback`` bars *before* each bar (signal bar excluded)."""
    s = np.asarray(high, dtype=float)
    out = np.full(s.shape, np.nan)
    for i in range(lookback, len(s)):
        out[i] = s[i - lookback : i].max()
    return out


def previous_low(low: np.ndarray, lookback: int = 20) -> np.ndarray:
    """min(low) of the ``lookback`` bars *before* each bar (signal bar excluded)."""
    s = np.asarray(low, dtype=float)
    out = np.full(s.shape, np.nan)
    for i in range(lookback, len(s)):
        out[i] = s[i - lookback : i].min()
    return out
