"""pathA-r10a and futures-r11 forward books (docs/band-books-plan.md)."""

import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.risk.sizing import RiskProfile
from trading.shadow.band_books import BandPerpBook, BandSpotBook
from trading.shadow.rebalance_book import PathABook
from trading.shadow.trend_perp_book import TrendPerpBook
from trading.strategies.sma_band import band_state

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "spike"))

LOOSE = RiskProfile(daily_loss_trigger_fraction=Decimal("0.99"), daily_loss_action="ALERT",
                    portfolio_drawdown_halt_fraction=None, max_gross_notional_to_equity=Decimal("1"),
                    requested_exchange_leverage=Decimal("1"), max_margin_utilization_fraction=Decimal("1"))


def _walk(n, seed):
    rng = np.random.default_rng(seed)
    return 30000 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))


def _market(prices, start="2026-01-01"):
    """Daily bars whose 01:00 open equals the day's open, so the books (fill at the first 1h
    bar after the close) and the research simulators (fill at the daily open) see one price."""
    idx = pd.date_range(start, periods=len(prices), freq="D", tz="UTC")
    opens = np.r_[prices[0], prices[:-1]]
    daily = pd.DataFrame({"open": opens, "close": prices, "high": np.maximum(opens, prices),
                          "low": np.minimum(opens, prices), "valid": True}, index=idx)
    hours = pd.date_range(idx[0], idx[-1] + pd.Timedelta(hours=23), freq="h", tz="UTC")
    hourly = pd.DataFrame({"open": daily["open"].reindex(hours.floor("D")).to_numpy()}, index=hours)
    return daily, hourly


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_latch_is_the_research_rule(seed):
    import run_r10
    import run_r11

    prices = _walk(700, seed)
    prices[[150, 151, 400]] = np.nan                       # invalid days keep the state
    daily, _ = _market(prices)
    c = daily["close"]
    sma = c.rolling(100, min_periods=100).mean()
    spot = run_r10.latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * 1.02, lambda i: c.iloc[i] < sma.iloc[i] * 0.98)
    assert band_state(c, signed=False).equals(spot)
    assert (band_state(c, signed=True) == run_r11.band_side(daily)).all()


def test_latch_from_the_fetched_window_matches_the_full_history():
    c = pd.Series(_walk(1500, 9), index=pd.date_range("2020-01-01", periods=1500, freq="D"))
    for signed, window in ((False, 400), (True, 300)):
        full = band_state(c, signed=signed)
        for k in range(window + 100, len(c), 7):
            assert band_state(c.iloc[k - window + 1:k + 1], signed=signed).iloc[-1] == full.iloc[k]


def test_spot_book_matches_simulate_weights_with_r10a_weights():
    from trading.strategies.funding_overlay_v3 import simulate_weights

    daily, hourly = _market(_walk(600, 2))
    w = band_state(daily["close"], signed=False)            # funding factor 1 (no hot funding)
    first = w.first_valid_index()
    book = BandSpotBook(profile=LOOSE)
    at = first + pd.Timedelta(days=1)
    book.open(str(first.date()), float(w.loc[first]), at, at, ["open"])
    for d in daily.index[daily.index > first]:
        book.advance(daily.loc[:d], hourly)
        close = d + pd.Timedelta(days=1)
        book.decide(str(d.date()), float(w.loc[d]), close, close, ["rule"])
    naive = daily.copy()
    naive.index = naive.index.tz_localize(None)
    s, e = (first + pd.Timedelta(days=1)).tz_localize(None), (daily.index[-1] + pd.Timedelta(days=1)).tz_localize(None)
    eq, trades = simulate_weights(naive, pd.Series(w.to_numpy(), index=naive.index), s, e)
    ours = np.array([float(r["equity"]) for r in book.history])
    np.testing.assert_allclose(ours, eq.iloc[1:].to_numpy(), rtol=1e-6)
    paper = [t for t in book.trades if t["book"] == "paper"]
    assert len(paper) == len(trades) > 3
    assert [t["side"] for t in paper] == list(trades["side"])


def test_perp_book_matches_run_r7_with_r11_sides():
    import run_r7
    import run_r11

    daily, hourly = _market(_walk(420, 6))
    funding = pd.Series(0.0002, index=daily.index)
    side = band_state(daily["close"], signed=True)
    first = daily.index[0]
    book = BandPerpBook(profile=LOOSE)
    at = first + pd.Timedelta(days=1)
    book.open(str(first.date()), int(side.iloc[0]), at, at, ["open"])
    for d in daily.index[1:]:
        book.advance(daily.loc[:d], hourly, funding)
        close = d + pd.Timedelta(days=1)
        book.decide(str(d.date()), int(side.loc[d]), close, close, ["rule"])
    r7_side = run_r7.side_state
    try:
        run_r7.side_state = run_r11.band_side
        s, e = str(daily.index[1].date()), str((daily.index[-1] + pd.Timedelta(days=1)).date())
        eq, trades, _ = run_r7.simulate(daily, funding, s, e)
    finally:
        run_r7.side_state = r7_side
    ours = np.array([float(r["equity"]) for r in book.history])
    np.testing.assert_allclose(ours, eq.iloc[1:].to_numpy(), rtol=1e-6)
    opens = [t for t in book.trades if t["book"] == "paper" and t["action"] == "open"]
    assert len(opens) == int((trades["action"] == "open").sum()) > 2


def test_state_files_only_load_into_their_own_book():
    daily, hourly = _market(_walk(5, 1))
    at = daily.index[0] + pd.Timedelta(days=1)
    pairs = [(BandSpotBook, PathABook, 1.0), (BandPerpBook, TrendPerpBook, 1)]
    for new, old, target in pairs:
        b = new()
        b.open(str(daily.index[0].date()), target, at, at, ["open"])
        state = b.to_dict()
        assert state["rules_version"] in ("pathA-r10a", "futures-r11")
        assert new(state).to_dict() == state
        with pytest.raises(ValueError):
            old(state)
        o = old()
        o.open(str(daily.index[0].date()), target, at, at, ["open"])
        with pytest.raises(ValueError):
            new(o.to_dict())
    assert PathABook().to_dict()["rules_version"] == "pathA-v1.2"
    assert TrendPerpBook().to_dict()["rules_version"] == "futures-r7"
