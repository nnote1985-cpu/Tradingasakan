import pandas as pd
import pytest

from trading.strategies.funding_overlay_v3 import OverlayParams, funding_weights, simulate_weights
from trading.strategies.trend_regime_v2 import RegimeCosts

D0 = pd.Timestamp("2023-01-01", tz="UTC")
NO_COST = RegimeCosts(fee=0.0, slip_bps=0.0)


def _daily(opens, closes):
    idx = pd.date_range(D0, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open": opens, "close": closes, "valid": True}, index=idx)


def test_weight_drops_when_trailing_mean_above_threshold():
    days = pd.date_range(D0, periods=5, freq="1D", tz="UTC")
    f = pd.Series([0.0003, 0.0003, 0.0021, 0.0021, 0.0], index=days)
    w = funding_weights(f, days, OverlayParams(window_days=2, threshold_per_day=0.0009))
    # means: nan, .0003, .0012, .0021, .00105
    assert w.tolist() == [1.0, 1.0, 0.5, 0.5, 0.5]


def test_rebalances_only_the_difference_at_next_open():
    d = _daily([100, 100, 200, 200], [100, 100, 200, 200])
    w = pd.Series([1.0, 0.5, 0.5, 1.0], index=d.index)
    eq, tr = simulate_weights(d, w, d.index[0], d.index[-1] + pd.Timedelta(days=1), costs=NO_COST)
    assert tr["side"].tolist() == ["BUY", "SELL"]   # the day-3 decision trades on day 4
    # day0 buy 100 BTC-units; day2 open sell half at 200 → 10000 cash + 50 units.
    assert tr["time"].iloc[1] == d.index[2]
    assert eq.iloc[3] == pytest.approx(10_000 + 50 * 200)


def test_full_weight_matches_buy_and_hold():
    d = _daily([100, 110, 120], [105, 115, 130])
    w = pd.Series(1.0, index=d.index)
    costs = RegimeCosts(fee=0.001, slip_bps=2)
    eq, tr = simulate_weights(d, w, d.index[0], d.index[-1] + pd.Timedelta(days=1), costs=costs)
    assert len(tr) == 1
    assert eq.iloc[-1] == pytest.approx(10_000 * 0.999 / (100 * 1.0002) * 130)
