import numpy as np
import pandas as pd
import pytest

from trading.features.indicators import atr_wilder, ema, previous_high, previous_low
from trading.strategies.trend_breakout_v1 import (
    StrategyParams, build_features, candidate_signal, features_row,
)


def test_ema_seeded_with_sma_then_alpha():
    out = ema(np.array([1, 2, 3, 4, 5], float), 3)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(2.0)          # SMA seed
    assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2)
    assert out[4] == pytest.approx(0.5 * 5 + 0.5 * 3)


def test_ema_restarts_after_gap():
    out = ema(np.array([1, 2, np.nan, 3, 4], float), 2)
    assert out[1] == pytest.approx(1.5)
    assert np.isnan(out[2]) and np.isnan(out[3])
    assert out[4] == pytest.approx(3.5)


def test_atr_wilder_seed_and_smoothing():
    h = np.array([10, 12, 13, 12, 15], float)
    l = np.array([9, 10, 11, 10, 12], float)
    c = np.array([9.5, 11, 12, 11, 14], float)
    out = atr_wilder(h, l, c, n=3)
    tr = [np.nan, 2.5, 2.0, 2.0, 4.0]
    assert out[3] == pytest.approx(np.mean(tr[1:4]))
    assert out[4] == pytest.approx((out[3] * 2 + tr[4]) / 3)


def test_breakout_levels_exclude_signal_bar():
    h = np.array([1, 2, 3, 10], float)
    l = np.array([5, 4, 3, 0], float)
    assert previous_high(h, 3)[3] == 3
    assert previous_low(l, 3)[3] == 3


T01 = {"quality": "VALID", "warmup_ok": True, "close4h": 59800, "ema50_4h": 59000,
       "ema200_4h": 57000, "close1h": 60000, "previous20_high": 59900,
       "previous20_low": 58000, "ema20_1h": 59400, "atr14_1h": 300}


def test_T01_long_candidate():
    assert candidate_signal(T01) == "LONG"


def test_T02_short_candidate():
    f = {"quality": "VALID", "warmup_ok": True, "close4h": 57000, "ema50_4h": 58000,
         "ema200_4h": 60000, "close1h": 56000, "previous20_high": 58000,
         "previous20_low": 56100, "ema20_1h": 56500, "atr14_1h": 300}
    assert candidate_signal(f) == "SHORT"


def test_T03_equal_to_breakout_level_is_no_trade():
    assert candidate_signal({**T01, "close1h": 59900}) == "NO_TRADE"


@pytest.mark.parametrize("patch", [
    {"atr14_1h": 0}, {"close1h": float("nan")}, {"quality": "INVALID"}, {"warmup_ok": False},
])
def test_T05_bad_data_is_no_trade(patch):
    assert candidate_signal({**T01, **patch}) == "NO_TRADE"


def _synthetic_h1(hours: int, seed: int = 7) -> pd.DataFrame:
    """Random-walk bars for unit tests only — never used for research results."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=hours, freq="1h", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.006, hours)))
    open_ = np.concatenate(([close[0]], close[:-1]))
    spread = np.abs(rng.normal(0, 0.003, hours)) * close
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close,
                         "volume": 1.0}, index=idx)


SMALL = StrategyParams(ema_fast_4h=5, ema_slow_4h=10, ema_local_1h=5, atr_n=5,
                       breakout_lookback=5, warmup_4h_bars=12, warmup_1h_bars=30)


def test_T04_uses_latest_closed_4h_bar_only():
    h1, h4 = build_features(_synthetic_h1(200), SMALL)
    # 1h bar 05:00–06:00 closes at 06:00; the 08:00 4h bar is still open,
    # so it must see the 00:00–04:00 bar, whose close is the 03:00 1h close.
    ts = pd.Timestamp("2024-01-03 05:00", tz="UTC")
    assert h1.loc[ts, "close4h"] == h1.loc[pd.Timestamp("2024-01-03 03:00", tz="UTC"), "close"]
    # 1h bar 03:00–04:00 closes exactly when the 00:00 4h bar closes.
    ts2 = pd.Timestamp("2024-01-03 03:00", tz="UTC")
    assert h1.loc[ts2, "close4h"] == h1.loc[ts2, "close"]


def test_gap_hour_invalidates_bar_and_4h_bucket():
    raw = _synthetic_h1(200).drop(pd.Timestamp("2024-01-05 09:00", tz="UTC"))
    h1, h4 = build_features(raw, SMALL)
    assert not h1.loc[pd.Timestamp("2024-01-05 09:00", tz="UTC"), "valid"]
    assert not h4.loc[pd.Timestamp("2024-01-05 08:00", tz="UTC"), "valid"]
    assert h1.loc[pd.Timestamp("2024-01-05 09:00", tz="UTC"), "signal"] == "NO_TRADE"


def test_vector_signal_matches_scalar_contract():
    h1, _ = build_features(_synthetic_h1(3000, seed=3), SMALL)
    assert (h1["signal"] != "NO_TRADE").sum() > 10
    for ts in h1.index[::7]:
        assert h1.loc[ts, "signal"] == candidate_signal(features_row(h1, ts)), ts
