"""Deterministic indicators: the one implementation strategies and the chart API share.

All functions take a 1-D float array (or Series) of *closed, validated* bars and
return an array of the same length. Positions before the indicator is defined are NaN.

``ema`` / ``ema_sma_seed``, ``true_range``, ``atr_wilder`` and ``previous_high/low`` are
pinned by BUILDPLAN v4.2 §6.1. ``sma``, ``band_latch``, ``ema_mt5``, ``adx_mt5`` and
``donchian`` were moved here from ``strategies/sma_band.py`` and ``strategies/argus_e1.py``
(docs/m2-charts-plan.md §4.1) with the same numpy/pandas operations in the same order, so
they return the same floats bit for bit; the old names stay as thin wrappers.

There are two EMA conventions and they give different values. ``ema_sma_seed`` seeds with the
mean of the first ``n`` values; ``ema_mt5`` (pandas ``ewm(adjust=False)``, as MetaTrader's iMA)
seeds with the first value. E1 uses ``ema_mt5``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


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


ema_sma_seed = ema


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


def sma(values: pd.Series | np.ndarray, n: int) -> np.ndarray:
    """Simple moving average over the last ``n`` values; NaN until ``n`` values exist and on any
    window holding a NaN (a missing day blocks the window, it is not skipped)."""
    return pd.Series(values).astype(float).rolling(n, min_periods=n).mean().to_numpy()


def band_latch(close: pd.Series, *, signed: bool, sma_n: int, band: float) -> pd.Series:
    """Latch state at each close around SMA(``sma_n``) with a ±``band`` hysteresis.

    signed=False (R10a): 1 in / 0 out; NaN before SMA exists and on days with no close or SMA
    (the state carries through them). signed=True (R11): +1 long / -1 short, 0 before SMA
    exists; invalid days keep the previous side. In above SMA × (1 + band), out below SMA × (1 − band).
    """
    c = close.astype(float).to_numpy()
    s = close.astype(float).rolling(sma_n, min_periods=sma_n).mean().to_numpy()
    out = np.full(len(c), np.nan if not signed else 0.0)
    state: float | None = None
    lo = -1.0 if signed else 0.0
    for i in range(len(c)):
        ok = np.isfinite(c[i]) and np.isfinite(s[i])
        if ok:
            if state is None:
                state = 1.0 if c[i] > s[i] else (lo if c[i] < s[i] or not signed else 0.0)
            elif state != 1.0 and c[i] > s[i] * (1 + band):
                state = 1.0
            elif state != lo and c[i] < s[i] * (1 - band):
                state = lo
        if signed:
            out[i] = state or 0.0
        elif ok:
            out[i] = state
    return pd.Series(out, index=close.index)


def ema_mt5(x: np.ndarray, n: int) -> np.ndarray:
    """EMA with alpha = 2/(n+1) seeded at the first value (MetaTrader iMA / pandas ewm adjust=False)."""
    return pd.Series(x).ewm(alpha=2.0 / (n + 1), adjust=False).mean().to_numpy(copy=True)


def adx_mt5(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    """MT5 iADX main line (EMA smoothing of +DI/−DI and DX); NaN for the first 2n bars."""
    ph, pl, pc = np.r_[np.nan, h[:-1]], np.r_[np.nan, lo[:-1]], np.r_[np.nan, c[:-1]]
    pdm, ndm = h - ph, pl - lo
    pdm, ndm = np.where(pdm < 0, 0.0, pdm), np.where(ndm < 0, 0.0, ndm)
    keep_p, keep_n = pdm > ndm, ndm > pdm
    pdm, ndm = np.where(keep_p, pdm, 0.0), np.where(keep_n, ndm, 0.0)
    tr = np.fmax(h, pc) - np.fmin(lo, pc)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_raw = np.where(tr > 0, 100.0 * pdm / tr, 0.0)
        n_raw = np.where(tr > 0, 100.0 * ndm / tr, 0.0)
    p_raw[0] = n_raw[0] = 0.0
    pdi, mdi = ema_mt5(p_raw, n), ema_mt5(n_raw, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(pdi + mdi > 0, 100.0 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    out = ema_mt5(dx, n)
    out[: 2 * n] = np.nan
    return out


def donchian(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(max high, min low) of the last ``n`` bars *including* the current one; NaN until n bars.

    ``previous_high/low`` are the same channel shifted by one bar (signal bar excluded)."""
    upper = pd.Series(high).rolling(n, min_periods=n).max().to_numpy()
    lower = pd.Series(low).rolling(n, min_periods=n).min().to_numpy()
    return upper, lower
