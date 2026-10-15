import pandas as pd
import pytest

from trading.paper.ledger import advance, decide, new_state, performance

D0 = pd.Timestamp("2025-01-01", tz="UTC")


def T(s):
    return pd.Timestamp(s, tz="UTC")


def _daily(closes, start=D0):
    idx = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open": closes, "close": closes}, index=idx)


def _hourly(days, price_at):
    idx = pd.date_range(D0, periods=24 * days, freq="1h", tz="UTC")
    return pd.DataFrame({"open": [price_at(t) for t in idx]}, index=idx)


def test_fill_waits_for_first_hour_after_decision_completed():
    # data ready 08:20:05, decided 08:20:09 on the 01-01 close -> fill at 01-02 09:00 open.
    st = new_state("2025-01-01", 1.0, T("2025-01-02 08:20:05"), T("2025-01-02 08:20:09"), ["open"],
                   info={"funding_proxy_days": 0, "funding_source": "archive"})
    o = st["pending"]
    assert o["fill_at"] == "2025-01-02T09:00:00+00:00"
    assert o["data_ready_at"] < o["decided_at"] < o["fill_at"]
    assert st["journal"][0]["funding_source"] == "archive"
    d = _daily([100, 120, 130])
    h = _hourly(3, lambda t: 110.0 if t < T("2025-01-02 09:00") else 115.0)
    added, problems = advance(st, d, h)
    assert not problems and [e["kind"] for e in added] == ["trade"]
    buy = st["trades"][0]
    assert buy["at"] == "2025-01-02T09:00:00+00:00" and buy["decided_at"] == o["decided_at"]
    assert buy["price"] == pytest.approx(115.0 * (1 + 2e-4))   # not the 00:00 open of 110
    bh = [t for t in st["trades"] if t["book"] == "buy_and_hold"]
    assert bh[0]["at"] == buy["at"]


def test_decision_on_the_hour_fills_at_the_next_hour():
    st = new_state("2025-01-01", 1.0, T("2025-01-02 08:59:58"), T("2025-01-02 09:00:00"), [])
    assert st["pending"]["fill_at"] == "2025-01-02T10:00:00+00:00"


def test_decided_before_data_ready_is_refused():
    with pytest.raises(ValueError):
        new_state("2025-01-01", 1.0, T("2025-01-02 08:21"), T("2025-01-02 08:20"), [])


def test_fill_not_after_decision_is_a_simulator_error():
    st = new_state("2025-01-01", 1.0, T("2025-01-02 08:20"), T("2025-01-02 08:21"), [])
    st["pending"]["fill_at"] = "2025-01-02T08:00:00+00:00"          # corrupted order
    added, problems = advance(st, _daily([100, 120]), _hourly(2, lambda t: 100.0))
    assert any("simulator error" in p for p in problems)
    assert not st["trades"] and st["last_day"] == "2025-01-01"


def test_fill_hour_missing_stops_without_marking():
    st = new_state("2025-01-01", 1.0, T("2025-01-02 08:20"), T("2025-01-02 08:20:04"), [])
    d = _daily([100, 120])
    added, problems = advance(st, d, _hourly(1, lambda t: 100.0))   # only 01-01 hours exist
    assert problems and st["last_day"] == "2025-01-01" and st["pending"] is not None


def test_missing_day_stops_the_book():
    st = new_state("2025-01-01", 1.0, T("2025-01-01 23:58"), T("2025-01-01 23:58:30"), [])
    d = _daily([100, 101, 102, 103]).drop(D0 + pd.Timedelta(days=2))   # 01-03 missing
    added, problems = advance(st, d, _hourly(4, lambda t: 100.0))
    assert st["last_day"] == "2025-01-02"
    assert any("2025-01-03" in p for p in problems)
    # deciding on a later close than the book has reached is refused.
    assert decide(st, "2025-01-04", 0.0, T("2025-01-05 08:20"), T("2025-01-05 08:21"), []) is None


def test_no_data_means_no_new_decision_and_decisions_are_frozen():
    st = new_state("2024-12-31", 1.0, T("2024-12-31 23:30"), T("2024-12-31 23:30:10"), [])
    advance(st, _daily([100]), _hourly(1, lambda t: 100.0))
    assert st["weight"] == 1.0 and st["last_day"] == "2025-01-01"
    e = decide(st, "2025-01-01", None, T("2025-01-02 08:20"), T("2025-01-02 08:20:03"), ["funding 7 วันไม่ครบ"])
    assert e["kind"] == "no_decision" and st["pending"] is None and e["decided_at"].startswith("2025-01-02T08:20:03")
    # a second run on the same close does not add another decision.
    assert decide(st, "2025-01-01", 0.0, T("2025-01-02 09:20"), T("2025-01-02 09:21"), []) is None
    assert performance(st)["weight_held"] == 1.0


def test_funding_policy_threshold_matches_the_signal_rule():
    from trading.alerts.live_data import FUNDING_POLICY
    from trading.alerts.risk_check import FUNDING_THRESHOLD_PER_DAY

    assert FUNDING_POLICY["version"] == "funding_policy.v1"
    assert FUNDING_POLICY["threshold_per_day"] == FUNDING_THRESHOLD_PER_DAY


def test_decision_snapshot_is_kept_in_the_journal():
    info = {"funding_policy": "funding_policy.v1", "funding_proxy_days": 2, "funding_source": "x",
            "inputs": {"funding_window": [{"day": "2025-01-01", "funding": 0.0001, "source": "proxy"}]},
            "inputs_sha256": "abc"}
    st = new_state("2025-01-01", 1.0, T("2025-01-02 08:20"), T("2025-01-02 08:20:01"), [], info=info)
    e = st["journal"][0]
    assert e["inputs"] == info["inputs"] and e["inputs_sha256"] == "abc" and e["funding_proxy_days"] == 2
