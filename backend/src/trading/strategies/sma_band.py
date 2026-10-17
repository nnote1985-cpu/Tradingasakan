"""SMA100 band latch for R10a (spot) and R11 (perp), docs/band-books-plan.md.

The research rules are ``research/spike/run_r10.py`` (``latch``) and ``run_r11.py``
(``band_side``); this is one implementation of both, checked against them in the tests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SMA_N = 100
BAND = 0.02


def band_state(close: pd.Series, *, signed: bool, sma_n: int = SMA_N, band: float = BAND) -> pd.Series:
    """Latch state at each daily close.

    signed=False (R10a): 1 in / 0 out; NaN before SMA exists and on days with no close or SMA
    (the state carries through them). signed=True (R11): +1 long / -1 short, 0 before SMA
    exists; invalid days keep the previous side.
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
