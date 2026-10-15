import pandas as pd
import pytest

from trading.paper.ledger import advance, new_state, performance

D0 = pd.Timestamp("2025-01-01", tz="UTC")


def _daily(opens, closes):
    idx = pd.date_range(D0, periods=len(opens), freq="1D", tz="UTC")
    return pd.DataFrame({"open": opens, "close": closes}, index=idx)


def _sig(daily, weights):
    return pd.DataFrame({"suggested_btc_weight": weights}, index=daily.index)


def test_weight_decided_at_close_trades_next_open_and_is_frozen():
    d = _daily([100, 100, 110, 120], [100, 110, 120, 90])
    st = new_state("2025-01-01", 1.0, ["open"])
    added = advance(st, d, _sig(d, [1.0, 1.0, 0.0, 0.0]))
    # day 2 buys at 100, day 3 holds (weight 0 decided at day 3 close), day 4 sells at 120 open.
    paper = [t for t in st["trades"] if t["book"] == "paper"]
    assert [(t["date"], t["side"]) for t in paper] == [("2025-01-02", "BUY"), ("2025-01-04", "SELL")]
    assert [e["kind"] for e in added] == ["trade", "decision", "trade"]
    assert st["history"][-1]["weight_held"] == 0.0
    # after selling at 120, the 90 close does not hurt the book; buy & hold falls.
    p = performance(st)
    assert p["equity"] == pytest.approx(10_000 * 1.2 * (1 - 0.001) ** 2 * (1 - 2e-4) / (1 + 2e-4))
    assert p["bh_return"] < 0 < p["return"]

    # rerunning with revised signals for processed days changes nothing.
    before = st["history"][:]
    advance(st, d, _sig(d, [0.5, 0.5, 0.5, 0.5]))
    assert st["history"] == before


def test_waits_for_complete_day():
    d = _daily([100, float("nan")], [100, float("nan")])
    st = new_state("2024-12-31", 1.0, [])
    advance(st, d, _sig(d, [1.0, 1.0]))
    assert st["last_day"] == "2025-01-01" and len(st["history"]) == 1
