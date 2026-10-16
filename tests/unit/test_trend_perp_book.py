"""futures-r7 book (docs/futures-r7-book-plan.md)."""

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.shadow.trend_perp_book import TrendPerpBook, side_signal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "spike"))


def _market(prices, start="2026-01-01"):
    """Daily bars whose 01:00 open equals the day's open, so the book (fills at the first
    1h bar after the close) and the R7 backtest (fills at the daily open) see one price."""
    idx = pd.date_range(start, periods=len(prices), freq="D", tz="UTC")
    opens = np.r_[prices[0], prices[:-1]]
    daily = pd.DataFrame({"open": opens, "close": prices, "high": np.maximum(opens, prices),
                          "low": np.minimum(opens, prices), "valid": True}, index=idx)
    hours = pd.date_range(idx[0], idx[-1] + pd.Timedelta(hours=23), freq="h", tz="UTC")
    hourly = pd.DataFrame({"open": daily["open"].reindex(hours.floor("D")).to_numpy()}, index=hours)
    return daily, hourly


def _run(daily, hourly, funding, profile=None):
    sig = side_signal(daily["close"])
    first = daily.index[0]
    book = TrendPerpBook(profile=profile) if profile else TrendPerpBook()
    at = first + pd.Timedelta(days=1)
    book.open(str(first.date()), int(sig.iloc[0]), at, at, ["open"])
    for d in daily.index[1:]:
        book.advance(daily.loc[:d], hourly, funding)
        close = d + pd.Timedelta(days=1)
        book.decide(str(d.date()), int(sig.loc[d]), close, close, ["rule"])
    return book


def _walk(n=320, seed=3):
    rng = np.random.default_rng(seed)
    return 30000 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))


def test_book_matches_the_r7_backtest_while_no_limit_trips():
    import run_r7

    from trading.risk.sizing import RiskProfile
    prices = _walk()
    daily, hourly = _market(prices)
    funding = pd.Series(0.0003, index=daily.index)
    loose = RiskProfile(daily_loss_trigger_fraction=Decimal("0.99"), daily_loss_action="ALERT",
                        portfolio_drawdown_halt_fraction=None, max_gross_notional_to_equity=Decimal("1"),
                        requested_exchange_leverage=Decimal("1"), max_margin_utilization_fraction=Decimal("1"))
    book = _run(daily, hourly, funding, loose)
    # The backtest's first traded day is the day after the first decision.
    s, e = str(daily.index[1].date()), str((daily.index[-1] + timedelta(days=1)).date())
    eq, trades, st = run_r7.simulate(daily, funding, s, e)
    ours = pd.Series([float(r["equity"]) for r in book.history], index=pd.to_datetime([r["date"] for r in book.history], utc=True))
    np.testing.assert_allclose(ours.to_numpy(), eq.iloc[1:].to_numpy(), rtol=1e-6)
    opens = [t for t in book.trades if t["book"] == "paper" and t["action"] == "open"]
    assert len(opens) == int((trades["action"] == "open").sum()) > 3
    assert book.book.check() == [] and book.bh.check() == []


def test_shorts_receive_positive_funding_and_longs_pay_it():
    prices = np.r_[np.linspace(100, 200, 130), np.linspace(200, 120, 60)]
    daily, hourly = _market(prices)
    book = _run(daily, hourly, pd.Series(0.001, index=daily.index))
    fund = [e for e in book.book.entries if e.kind == "funding"]
    paid = {e.data["qty"][0] == "-": Decimal(e.data["paid"]) for e in fund}
    assert paid[False] > 0 and paid[True] < 0           # long paid, short received


def test_no_fill_at_or_before_the_decision_and_missing_hour_waits():
    prices = np.r_[np.linspace(100, 200, 120)]
    daily, hourly = _market(prices)
    book = _run(daily, hourly, pd.Series(0.0, index=daily.index))
    for t in book.trades:
        if t["book"] == "paper":
            assert pd.Timestamp(t["at"]) > pd.Timestamp(t["decided_at"])
    # A pending order whose 1h bar is missing makes the book wait.
    b2 = TrendPerpBook()
    first = daily.index[0]
    at = first + pd.Timedelta(days=1)
    b2.open(str(first.date()), 1, at, at, ["open"])
    _, problems = b2.advance(daily, hourly.drop(at + pd.Timedelta(hours=1)), pd.Series(dtype=float))
    assert problems and "cannot fill yet" in problems[0] and b2.history == []


def test_replay_changes_nothing_and_state_round_trips():
    prices = _walk(200, seed=5)
    daily, hourly = _market(prices)
    f = pd.Series(0.0002, index=daily.index)
    book = _run(daily, hourly, f)
    before = book.to_dict()
    added, problems = book.advance(daily, hourly, f)
    assert added == [] and problems == []
    last = daily.index[-1] + pd.Timedelta(days=1)
    assert book.decide(book.last_day, 1, last, last, ["again"]) in (None, book.journal[-1])
    again = TrendPerpBook(book.to_dict())
    assert again.to_dict() == book.to_dict() and before["history"] == book.to_dict()["history"]


def test_halt_closes_the_position_survives_reload_and_resume_needs_a_name():
    prices = np.r_[np.linspace(100, 200, 130), np.full(10, 140.0)]     # a one-day 30% crash while long
    daily, hourly = _market(prices)
    book = _run(daily, hourly, pd.Series(0.0, index=daily.index))
    assert book.risk.halted and book.book.position.qty == 0
    assert any(e["kind"] == "halt" for e in book.journal) and any(e["kind"] == "ignored_halted" for e in book.journal)
    reloaded = TrendPerpBook(book.to_dict())
    assert reloaded.risk.halted
    with pytest.raises(ValueError):
        reloaded.resume(" ", "reason", daily.index[-1])
    e = reloaded.resume("Note", "test", daily.index[-1] + pd.Timedelta(days=1))
    assert e["kind"] == "resume" and not reloaded.risk.halted
