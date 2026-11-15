"""SMA100 band latch for R10a (spot) and R11 (perp), docs/band-books-plan.md.

The research rules are ``research/spike/run_r10.py`` (``latch``) and ``run_r11.py``
(``band_side``); this is one implementation of both, checked against them in the tests. The latch itself lives in
``trading.features.indicators.band_latch`` (shared with the chart API).
"""

from __future__ import annotations

import pandas as pd

from trading.features.indicators import band_latch

SMA_N = 100
BAND = 0.02


def band_state(close: pd.Series, *, signed: bool, sma_n: int = SMA_N, band: float = BAND) -> pd.Series:
    """Latch state at each daily close; see :func:`trading.features.indicators.band_latch`.

    signed=False (R10a): 1 in / 0 out; NaN before SMA exists and on days with no close or SMA
    (the state carries through them). signed=True (R11): +1 long / -1 short, 0 before SMA
    exists; invalid days keep the previous side.
    """
    return band_latch(close, signed=signed, sma_n=sma_n, band=band)
