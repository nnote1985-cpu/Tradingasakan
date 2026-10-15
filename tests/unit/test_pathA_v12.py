"""pathA-v1.2 acceptance tests (docs/pathA-v1.2-plan.md)."""

import json
from dataclasses import replace
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.paper import ledger as v11
from trading.risk.profiles import SPOT_V1
from trading.shadow.rebalance_book import PathABook

D0 = pd.Timestamp("2025-01-01", tz="UTC")


def market(closes_per_day):
    """Hourly bars whose daily close is the given series (the price walks linearly within each day)."""
    rows, prev = [], closes_per_day[0]
    for d, c in enumerate(closes_per_day):
        path = np.linspace(prev, c, 25)[1:]
        opens = np.r_[prev, path[:-1]]
        for h in range(24):
            rows.append((D0 + pd.Timedelta(days=d, hours=h), opens[h], path[h]))
        prev = c
    hourly = pd.DataFrame(rows, columns=["t", "open", "close"]).set_index("t")
    daily = pd.DataFrame({"close": hourly["close"].resample("1D").last()})
    return daily, hourly


def stamps(day: pd.Timestamp):
    close = day + pd.Timedelta(days=1)
    return close + pd.Timedelta(minutes=5), close + pd.Timedelta(minutes=6)


def run_both(daily, weights):
    """Drive the v1.1 float book and the v1.2 book with the same decisions."""
    days = list(daily.index)
    r, dn = stamps(days[0])
    old = v11.new_state(str(days[0].date()), weights[0], r, dn, ["open"])
    new = PathABook()
    new.open(str(days[0].date()), weights[0], r, dn, ["open"])
    return old, new, days


def drive(old, new, days, daily, hourly, weights, upto=None):
    for k, day in enumerate(days[1:upto], start=1):
        sub_d, sub_h = daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)]
        v11.advance(old, sub_d, sub_h)
        new.advance(sub_d, sub_h)
        r, dn = stamps(day)
        v11.decide(old, str(day.date()), weights[k], r, dn, ["x"])
        new.decide(str(day.date()), weights[k], r, dn, ["x"])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_same_book_as_v11_while_no_limit_trips(seed):
    rng = np.random.default_rng(seed)
    n = 120
    closes = 60000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    daily, hourly = market(closes)
    weights = list(rng.choice([0.0, 0.5, 1.0], n))
    old, new, days = run_both(daily, weights)
    drive(old, new, days, daily, hourly, weights)
    assert not new.risk.halted
    po = [t for t in old["trades"]]
    pn = [t for t in new.trades]
    assert len(po) == len(pn) > 20
    for a, b in zip(po, pn):
        assert (a["book"], a["at"], a["side"]) == (b["book"], b["at"], b["side"])
        assert float(b["qty"]) == pytest.approx(a["qty"], rel=1e-9)
        assert float(b["price"]) == pytest.approx(a["price"], rel=1e-12)
    assert len(old["history"]) == len(new.history) == n - 1
    for a, b in zip(old["history"], new.history):
        assert a["date"] == b["date"]
        assert float(b["equity"]) == pytest.approx(a["equity"], rel=1e-6)
        assert float(b["buy_and_hold"]) == pytest.approx(a["buy_and_hold"], rel=1e-6)
    assert new.book.check() == [] and new.bh.check() == []


def test_fill_never_at_or_before_decision_and_waits_for_missing_bar():
    daily, hourly = market([60000] * 5)
    new = PathABook()
    r, dn = stamps(daily.index[0])
    new.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    gap = hourly.drop(daily.index[1] + pd.Timedelta(hours=1))
    _, problems = new.advance(daily, gap)
    assert problems and new.last_day == str(daily.index[0].date()) and new.pending is not None
    new.advance(daily, hourly)
    t = new.trades[0]
    assert pd.Timestamp(t["at"]) > pd.Timestamp(t["decided_at"])
    assert t["at"] == "2025-01-02T01:00:00+00:00"


def test_replaying_days_and_decisions_changes_nothing():
    daily, hourly = market(list(np.linspace(60000, 63000, 10)))
    new = PathABook()
    r, dn = stamps(daily.index[0])
    new.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    new.advance(daily, hourly)
    snap = json.dumps(new.to_dict())
    new.advance(daily, hourly)
    r, dn = stamps(daily.index[-1])
    assert new.decide(str(daily.index[-1].date()), 0.0, r, dn, ["x"]) is not None
    assert new.decide(str(daily.index[-1].date()), 0.5, r, dn, ["x"]) is None
    assert json.loads(snap)["trades"] == new.to_dict()["trades"]


def test_day_loss_alert_keeps_position():
    daily, hourly = market([60000, 60000, 56400, 56000])               # -6% on day 2
    new = PathABook()
    r, dn = stamps(daily.index[0])
    new.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    added, _ = new.advance(daily, hourly)
    alerts = [e for e in added if e["kind"] == "alert"]
    assert [a["alert"] for a in alerts] == ["DAILY_LOSS_TRADING"]
    assert not new.risk.halted and new.weight == 1.0 and new.book.position.qty > 0


def falling():
    return market([60000, 60000, 57000, 54000, 51000, 48000, 45000, 43000, 42000, 41000, 50000, 60000])


def test_halt_at_25pct_sells_to_cash_ignores_decisions_and_survives_restart():
    daily, hourly = falling()
    new = PathABook()
    r, dn = stamps(daily.index[0])
    new.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    new.advance(daily.iloc[:8], hourly.loc[:daily.index[7] + pd.Timedelta(hours=23)])
    assert new.risk.halted and new.risk.halt_reason == "DRAWDOWN"
    assert [a["alert"] for a in new.risk.alerts].count("DRAWDOWN") == 1          # the 15% alert came first
    halt = [e for e in new.journal if e["kind"] == "halt"][0]
    new = PathABook(json.loads(json.dumps(new.to_dict())))
    r, dn = stamps(daily.index[7])
    assert new.decide(str(daily.index[7].date()), 1.0, r, dn, ["buy"])["kind"] == "ignored_halted"
    new.advance(daily, hourly)
    sell = [t for t in new.trades if t.get("reason") == "halt"][0]
    assert sell["side"] == "SELL" and pd.Timestamp(sell["at"]) == pd.Timestamp(halt["fill_at"])
    assert new.book.position.qty == 0 and new.weight == 0.0 and new.risk.halted
    assert new.history[-1]["buy_and_hold"] != new.history[-1]["equity"]       # buy & hold is never halted


def test_resume_needs_a_name_and_restarts_the_high_water_mark():
    daily, hourly = falling()
    new = PathABook()
    r, dn = stamps(daily.index[0])
    new.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    new.advance(daily.iloc[:10], hourly.loc[:daily.index[9] + pd.Timedelta(hours=23)])
    with pytest.raises(ValueError):
        new.resume(" ", "x", daily.index[10])
    new.resume("Note", "reviewed the drawdown", daily.index[10])
    assert not new.risk.halted and new.risk.high_water_mark == new.book.bal("cash")
    r, dn = stamps(daily.index[9])
    assert new.decide(str(daily.index[9].date()), 1.0, r, dn, ["buy"])["kind"] == "decision"
    new.advance(daily, hourly)
    assert new.weight == 1.0 and not new.risk.halted
    assert [e["kind"] for e in new.journal].count("resume") == 1


def test_state_with_other_limits_is_refused():
    new = PathABook()
    with pytest.raises(ValueError):
        PathABook(new.to_dict(), profile=replace(SPOT_V1, portfolio_drawdown_halt_fraction=Decimal("0.3")))
