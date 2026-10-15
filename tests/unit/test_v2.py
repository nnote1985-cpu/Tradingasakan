import numpy as np
import pandas as pd
import pytest

from trading.strategies.trend_regime_v2 import RegimeCosts, daily_bars, regime_state, simulate

D0 = pd.Timestamp("2023-01-01", tz="UTC")
NO_COST = RegimeCosts(fee=0.0, slip_bps=0.0)


def _daily(opens, closes):
    idx = pd.date_range(D0, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open": opens, "close": closes, "valid": True}, index=idx)


def test_daily_bars_need_all_24_hours():
    idx = pd.date_range(D0, periods=48, freq="1h", tz="UTC")
    c = np.arange(48, dtype=float) + 100
    h1 = pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx).drop(idx[30])
    d = daily_bars(h1)
    assert d["valid"].tolist() == [True, False]
    assert d["open"].iloc[0] == 100 and d["close"].iloc[0] == 123


def test_state_uses_sma_of_closes_and_holds_through_warmup():
    d = _daily([1] * 5, [10, 10, 10, 13, 8])
    s = regime_state(d, sma_n=3)
    assert s.tolist() == [False, False, False, True, False]


def test_position_trades_at_next_open():
    d = _daily([100, 100, 100, 110, 120, 90], [100, 100, 105, 115, 95, 90])
    state = pd.Series([False, False, True, True, False, False], index=d.index)
    eq, trades = simulate(d, state, d.index[0], d.index[-1] + pd.Timedelta(days=1), costs=NO_COST)
    assert trades["time"].tolist() == [d.index[3], d.index[5]]
    assert trades["price"].tolist() == [110, 90]
    # Bought 10000/110 at day 3 open, sold at day 5 open (90).
    assert eq.iloc[-1] == pytest.approx(10_000 / 110 * 90)
    assert eq.iloc[0] == 10_000 and eq.index[0] == d.index[0] - pd.Timedelta(days=1)


def test_always_in_matches_buy_and_hold_with_costs():
    d = _daily([100, 101, 102], [101, 102, 104])
    state = pd.Series(True, index=d.index)
    costs = RegimeCosts(fee=0.001, slip_bps=2.0)
    # State before the first day is unknown → OUT on day 0, so start one day later.
    eq, trades = simulate(d, state, d.index[1], d.index[-1] + pd.Timedelta(days=1), costs=costs)
    qty = 10_000 * (1 - 0.001) / (101 * 1.0002)
    assert len(trades) == 1
    assert eq.iloc[-1] == pytest.approx(qty * 104)


def test_funding_is_paid_while_long():
    d = _daily([100, 100, 100], [100, 100, 100])
    state = pd.Series([True, True, True], index=d.index)
    f = pd.Series([0.001, 0.001, 0.001], index=d.index)
    eq, _ = simulate(d, state, d.index[1], d.index[-1] + pd.Timedelta(days=1), costs=NO_COST, funding_daily=f)
    assert eq.iloc[-1] == pytest.approx(10_000 - 2 * 10)
