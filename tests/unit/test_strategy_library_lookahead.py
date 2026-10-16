"""Lookahead and start-up checks for every strategy in the library (ideas from freqtrade's
lookahead-analysis and recursive-analysis).

Lookahead: cut the 1h history at an arbitrary hour (mid-bar included) and recompute. Every
weight whose bar closed by the cut must equal the weight from the full history, for every
strategy, timeframe and param set (defaults and both bounds).

Start-up: start the history later and recompute. Indicators like EMA and Wilder RSI depend on
where the history starts, so early weights may differ; they must agree with the full history
within ``WARMUP_BARS[no]`` bars of the timeframe. Feed a strategy at least that many bars before
trusting a weight. The numbers are the worst case measured over 3 seeds x 3 start offsets x 4h/1d
(rounded up by ~25%); EMA-based rules need about 3x their longest EMA (#5 with EMA 200: ~630 bars).
"""

import numpy as np
import pandas as pd
import pytest

from trading.strategies.library import catalog
from trading.strategies.library.bars import TIMEFRAMES, resample

T0 = pd.Timestamp("2022-01-01", tz="UTC")
# strategy number -> bars of history needed before its weight stops depending on the start
WARMUP_BARS = {1: 160, 2: 140, 3: 180, 4: 180, 5: 800, 6: 30, 7: 140, 8: 40, 9: 260, 10: 120,
               11: 110, 12: 20, 13: 40}


def _h1(n_hours: int, seed: int) -> pd.DataFrame:
    """Random-walk 1h OHLC with realistic wicks and a few missing hours."""
    rng = np.random.default_rng(seed)
    c = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.008, n_hours)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.001, n_hours))
    hi = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.004, n_hours)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.004, n_hours)))
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c},
                      index=pd.date_range(T0, periods=n_hours, freq="1h"))
    return df.drop(df.index[rng.choice(n_hours, n_hours // 500, replace=False)])


H1 = _h1(24 * 900, seed=3)
H1_LONG = _h1(24 * 1300, seed=5)  # 1d needs > WARMUP_BARS[5] bars after a late start


def _param_sets(spec: catalog.StrategySpec) -> list[dict]:
    lo = {k: p.min for k, p in spec.params.items()}
    hi = {k: p.max for k, p in spec.params.items()}
    return [{}, lo, hi]


@pytest.mark.parametrize("no", sorted(catalog.CATALOG))
@pytest.mark.parametrize("tf", sorted(TIMEFRAMES))
def test_weight_uses_only_bars_closed_by_then(no, tf):
    spec = catalog.get(no)
    cuts = H1.index[[len(H1) // 4 + 5, len(H1) // 2 + 13, 3 * len(H1) // 4 + 21]]
    for params in _param_sets(spec):
        full = spec.signal(resample(H1, tf), params, tf)
        for cut in cuts:  # cut = open time of the last 1h bar kept
            part = spec.signal(resample(H1[H1.index <= cut], tf), params, tf)
            part = part[part.index <= cut + pd.Timedelta(hours=1)]
            assert len(part) > 0
            pd.testing.assert_series_equal(full.loc[part.index], part, obj=f"#{no} {tf} {params} cut {cut}")


@pytest.mark.parametrize("no", sorted(catalog.CATALOG))
@pytest.mark.parametrize("tf", ["4h", "1d"])
def test_late_start_agrees_after_warmup(no, tf):
    spec = catalog.get(no)
    bars = resample(H1_LONG, tf)
    full = spec.signal(bars, timeframe=tf)
    warmup = WARMUP_BARS[no]
    for skip in (37, 200):
        late = spec.signal(resample(H1_LONG[H1_LONG.index >= bars.index[skip]], tf), timeframe=tf)
        assert len(late) > warmup + 100
        differs = (full.loc[late.index] != late).to_numpy()
        assert not differs[warmup:].any(), f"#{no} {tf} skip {skip}: still differs after {warmup} bars"


def test_every_strategy_has_a_warmup():
    assert set(WARMUP_BARS) == set(catalog.CATALOG)
