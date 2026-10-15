import pandas as pd
import pytest
from trading.paper.ledger import advance, decide, new_state, performance

D0 = pd.Timestamp("2025-01-01", tz="UTC")


def _daily(closes, start=D0):
    idx = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open": closes, "close": closes}, index=idx)


def _hourly(days, price_at):
    idx = pd.date_range(D0, periods=24 * days, freq="1h", tz="UTC")
    return pd.DataFrame({"open": [price_at(t) for t in idx]}, index=idx)


def test_fill_waits_for_first_hour_after_data_received():
    # decision on the 01-01 close, received 01-02 08:20 -> fill at 01-02 09:00 open.
    st = new_state("2025-01-01", 1.0, pd.Timestamp("2025-01-02 08:20", tz="UTC"), ["open"])
    assert st["pending"]["fill_at"] == "2025-01-02T09:00:00+00:00"
    d = _daily([100, 120, 130])
    h = _hourly(3, lambda t: 110.0 if t < pd.Timestamp("2025-01-02 09:00", tz="UTC") else 115.0)
    added, problems = advance(st, d, h)
    assert not problems and [e["kind"] for e in added] == ["trade"]
    buy = st["trades"][0]
    assert buy["at"] == "2025-01-02T09:00:00+00:00"
    assert buy["price"] == pytest.approx(115.0 * (1 + 2e-4))   # not the 00:00 open of 110
    bh = [t for t in st["trades"] if t["book"] == "buy_and_hold"]
    assert bh[0]["at"] == buy["at"]


def test_fill_hour_missing_stops_without_marking():
    st = new_state("2025-01-01", 1.0, pd.Timestamp("2025-01-02 08:20", tz="UTC"), [])
    d = _daily([100, 120])
    added, problems = advance(st, d, _hourly(1, lambda t: 100.0))   # only 01-01 hours exist
    assert problems and st["last_day"] == "2025-01-01" and st["pending"] is not None


def test_missing_day_stops_the_book():
    st = new_state("2025-01-01", 1.0, pd.Timestamp("2025-01-01 23:59", tz="UTC"), [])
    d = _daily([100, 101, 102, 103]).drop(D0 + pd.Timedelta(days=2))   # 01-03 missing
    added, problems = advance(st, d, _hourly(4, lambda t: 100.0))
    assert st["last_day"] == "2025-01-02"
    assert any("2025-01-03" in p for p in problems)
    # deciding on a later close than the book has reached is refused.
    assert decide(st, "2025-01-04", 0.0, pd.Timestamp("2025-01-05 08:20", tz="UTC"), []) is None


def test_no_data_means_no_new_decision_and_decisions_are_frozen():
    st = new_state("2024-12-31", 1.0, pd.Timestamp("2024-12-31 23:30", tz="UTC"), [])
    advance(st, _daily([100]), _hourly(1, lambda t: 100.0))
    assert st["weight"] == 1.0 and st["last_day"] == "2025-01-01"
    e = decide(st, "2025-01-01", None, pd.Timestamp("2025-01-02 08:20", tz="UTC"), ["funding 7 วันไม่ครบ"])
    assert e["kind"] == "no_decision" and st["pending"] is None
    # a second run on the same close does not add another decision.
    assert decide(st, "2025-01-01", 0.0, pd.Timestamp("2025-01-02 09:20", tz="UTC"), []) is None
    assert performance(st)["weight_held"] == 1.0
