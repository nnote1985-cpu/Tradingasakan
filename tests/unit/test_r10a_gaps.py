"""tn-r10a-code-v1.1: a daily close missing from the archive is filled from REST; the decided day is not."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from trading.runtime.r10a import decide, fill_gaps


def _daily(days: pd.DatetimeIndex) -> pd.DataFrame:
    close = np.linspace(100.0, 200.0, len(days))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=days)


def test_a_day_the_archive_skipped_is_filled_from_rest_and_listed():
    days = pd.date_range("2026-06-01", "2026-10-29", freq="D", tz="UTC")
    full = _daily(days)
    archive = full.drop(pd.Timestamp("2026-10-28", tz="UTC"))
    asked = []

    def rest(start: date, end: date) -> pd.DataFrame:
        asked.append((start, end))
        return full.loc[str(start):str(end)]

    out, filled = fill_gaps(archive, date(2026, 10, 29), rest)
    assert filled == ["2026-10-28"] and asked == [(date(2026, 10, 28), date(2026, 10, 28))]
    assert out.equals(full)


def test_the_decided_day_is_never_filled_and_no_gap_means_no_rest_call():
    days = pd.date_range("2026-06-01", "2026-10-28", freq="D", tz="UTC")
    archive = _daily(days)

    def rest(start: date, end: date) -> pd.DataFrame:
        raise AssertionError("no gap before the day: REST must not be called")

    out, filled = fill_gaps(archive, date(2026, 10, 29), rest)
    assert filled == [] and out.equals(archive)


def test_a_filled_gap_lets_the_day_be_decided_and_is_in_the_snapshot():
    days = pd.date_range("2026-06-01", "2026-10-29", freq="D", tz="UTC")
    full = _daily(days)
    archive = full.drop(pd.Timestamp("2026-10-28", tz="UTC"))
    funding, est = pd.Series(0.0001, index=days), pd.Series(True, index=days)
    at = datetime(2026, 10, 30, 8, tzinfo=UTC)
    stuck = decide(archive, funding, est, date(2026, 10, 29), at)
    assert stuck is not None and stuck.weight is None                   # the gap alone leaves no SMA100
    out, filled = fill_gaps(archive, date(2026, 10, 29), lambda s, e: full.loc[str(s):str(e)])
    d = decide(out, funding, est, date(2026, 10, 29), at, filled)
    assert d is not None and d.weight == 1 and d.inputs["rest_filled_days"] == ["2026-10-28"]


def test_the_day_waits_while_its_funding_is_not_out():
    days = pd.date_range("2026-06-01", "2026-10-29", freq="D", tz="UTC")
    funding = pd.Series(0.0001, index=days[:-1])
    est = pd.Series(True, index=days[:-1])
    assert decide(_daily(days), funding, est, date(2026, 10, 29), datetime(2026, 10, 30, 8, tzinfo=UTC)) is None


def test_rest_rows_parse_like_the_archive_and_drop_the_open_day(monkeypatch):
    import json

    from trading.market_data import binance_public
    from trading.runtime.r10a import rest_daily_klines

    day = int(pd.Timestamp("2026-10-28", tz="UTC").timestamp() * 1000)
    rows = [[day, "1", "3", "0.5", "2.5", "10", day + 86_399_999, "0", 1, "0", "0", "0"],
            [day + 86_400_000 * 1000, "1", "1", "1", "1", "1", day + 86_400_000 * 1001 - 1, "0", 1, "0", "0", "0"]]
    seen = []
    monkeypatch.setattr(binance_public, "_get", lambda url, timeout=60: seen.append(url) or json.dumps(rows).encode())
    df = rest_daily_klines(date(2026, 10, 28), date(2026, 10, 28))
    assert list(df.index) == [pd.Timestamp("2026-10-28", tz="UTC")]
    assert df.iloc[0].to_dict() == {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.5, "volume": 10.0}
    assert seen[0].startswith("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1d")
