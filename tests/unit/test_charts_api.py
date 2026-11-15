"""M2 PR 3: chart bars and indicators (docs/m2-charts-plan.md §5.1 I1/I2, §5.2 B2–B5)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from test_api_auth import STRANGER, URL, VIEWER, _get, _Jwks, _Roles, _token

from api.auth import SupabaseJwtVerifier
from api.charts import ArchiveCache, BadRequest, ChartService, Loading, complete_hours, four_hour
from api.main import create_app
from trading.config import Settings
from trading.contracts.enums import OperatorRole
from trading.evaluation.r10a_soak_replay import load_fixture
from trading.strategies import argus_e1

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "r10a_soak"
GAP_DAY = pd.Timestamp("2026-10-28", tz="UTC")
GAP_PUBLISHED = date(2026, 11, 11)


def _run(coro):
    return asyncio.run(coro)


def _ohlc(n: int, freq: str, start: str, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 80_000 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    o = np.r_[c[0], c[:-1]]
    spread = np.abs(rng.normal(0, 0.002, n)) * c
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c,
                         "volume": rng.uniform(1, 10, n)}, index=pd.date_range(start, periods=n, freq=freq, tz="UTC"))


class _Archive:
    """Archive loader over in-memory frames: {(venue, interval): DataFrame}."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.frames, self.calls = frames, 0

    def __call__(self, venue, interval, symbol, y, m, today):
        self.calls += 1
        df = self.frames.get((venue, interval))
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
        start = pd.Timestamp(date(y, m, 1), tz="UTC")
        end = (start + pd.Timedelta(days=32)).replace(day=1)
        return df[(df.index >= start) & (df.index < end)]


class _Store:
    def __init__(self, daily=None, minutes=None) -> None:
        self.d = daily if daily is not None else pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
        self.m = minutes

    async def daily(self, venue, symbol, start, end):
        return self.d[(self.d.index >= pd.Timestamp(start)) & (self.d.index < pd.Timestamp(end))]

    async def hourly(self, venue, symbol, start, end):
        if self.m is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "n"], dtype=float)
        m = self.m[(self.m.index >= pd.Timestamp(start)) & (self.m.index < pd.Timestamp(end))]
        g = m.groupby(m.index.floor("1h"))
        return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                             "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].size().astype(float)})


def _svc(frames, now, store=None) -> ChartService:
    clock = lambda: now  # noqa: E731
    return ChartService(store or _Store(), ArchiveCache(_Archive(frames), clock=clock), clock=clock)


# --- I1: R10a inputs ---------------------------------------------------------------------------


def test_r10a_indicators_equal_every_recorded_decision():
    rows, klines, _ = load_fixture(str(FIXTURE))
    checked = 0
    for row in rows:
        day = date.fromisoformat(row["day"])
        k = klines[klines.index <= pd.Timestamp(day, tz="UTC")]
        if day < GAP_PUBLISHED and not row["inputs"].get("rest_filled_days"):
            k = k[k.index != GAP_DAY]                  # the archive the worker saw (v1.1 filled the day from REST)
        svc = _svc({("perp", "1d"): k}, datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=9))
        a = pd.Timestamp(day, tz="UTC").to_pydatetime()
        body = _run(svc.indicators("BTCUSDT", "perp", "1d", a, a + timedelta(days=1), "r10a"))
        got = {s["key"]: s["values"][-1]["v"] for s in body["series"]}
        bars = _run(svc.bars("BTCUSDT", "perp", "1d", a, a + timedelta(days=1)))
        want = row["inputs"]
        if want["sma100"] is None:
            assert got["sma100"] is None and got["band_state"] is None, row["day"]
        else:
            assert float(got["sma100"]) == pytest.approx(want["sma100"], rel=1e-9, abs=0), row["day"]
            assert float(got["band_state"]) == want["band_state"], row["day"]
            assert float(bars["bars"][-1]["c"]) == want["close_perp"], row["day"]
            checked += 1
    assert checked == 8                                # 10-18, 10-19, 11-08 … 11-13


# --- I2: E1 ------------------------------------------------------------------------------------


def test_e1_indicators_equal_e1_signals_on_the_same_bars():
    h1 = _ohlc(24 * 30 * 20, "1h", "2025-01-01", seed=3)
    now = datetime(2026, 8, 20, 9, tzinfo=UTC)
    svc = _svc({("spot", "1h"): h1}, now)
    a, b = datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
    body = _run(svc.indicators("BTCUSDT", "spot", "4h", a, b, "e1"))
    # E1Signals on the same 4h bars the API loaded (2,100 warm-up bars before `from`)
    lo = pd.Timestamp(a) - 2_100 * pd.Timedelta(hours=4)
    h4 = argus_e1.h4_bars(h1[(h1.index >= lo) & (h1.index < pd.Timestamp(b))])
    sig = argus_e1.E1Signals(h4)
    keep = h4.index >= pd.Timestamp(a)
    want = {"ema50": sig.e50, "ema200": sig.e200, "adx14": sig.adx, "donchian20_upper": sig.hi20, "donchian20_lower": sig.lo20}
    got = {s["key"]: s for s in body["series"]}
    for key, arr in want.items():
        vals = np.array([np.nan if x["v"] is None else float(x["v"]) for x in got[key]["values"]])
        assert np.allclose(vals, arr[keep], rtol=1e-9, atol=0, equal_nan=True), key
    assert [x["t"] for x in got["ema50"]["values"]] == [int(t.timestamp()) for t in h4.index[keep]]


# --- bars --------------------------------------------------------------------------------------


def test_four_hour_needs_four_hours_and_matches_e1_h4_bars():
    h1 = _ohlc(24 * 5, "1h", "2026-10-01", seed=1)
    b = four_hour(h1)
    ref = argus_e1.h4_bars(h1)
    assert np.array_equal(b[["open", "high", "low", "close"]].to_numpy(), ref.to_numpy())
    holed = h1.drop(h1.index[5])
    assert pd.Timestamp("2026-10-01 04:00", tz="UTC") not in four_hour(holed).index


def test_minutes_make_hours_only_when_all_sixty_are_present():
    m1 = _ohlc(180, "1min", "2026-11-01", seed=2)
    hours = _run(_Store(minutes=m1.drop(m1.index[70])).hourly("v", "s", m1.index[0], m1.index[-1] + pd.Timedelta(minutes=1)))
    full = complete_hours(hours)
    assert list(full.index.hour) == [0, 2]
    h0 = m1.iloc[:60]
    assert full.iloc[0].tolist()[:4] == [h0["open"].iloc[0], h0["high"].max(), h0["low"].min(), h0["close"].iloc[-1]]
    assert full.iloc[0]["volume"] == pytest.approx(h0["volume"].sum(), rel=1e-12)


def test_spot_hours_use_db_then_archive_and_leave_gaps():
    now = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    m1 = _ohlc(60 * 6 + 30, "1min", "2026-11-01", seed=4)
    m1 = m1[~((m1.index >= "2026-11-01 01:00") & (m1.index < "2026-11-01 03:00"))]   # worker down 01:00–03:00
    arch = _ohlc(24, "1h", "2026-10-31 23:00", seed=5).drop(pd.Timestamp("2026-11-01 02:00", tz="UTC"))
    svc = _svc({("spot", "1h"): arch}, now, _Store(minutes=m1))
    body = _run(svc.bars("BTCUSDT", "spot", "1h", datetime(2026, 11, 1, tzinfo=UTC), None))
    ts = [b["t"] for b in body["bars"]]
    hour = lambda h: int(datetime(2026, 11, 1, h, tzinfo=UTC).timestamp())  # noqa: E731
    assert ts == [hour(0), hour(1), hour(3), hour(4), hour(5)]                  # 02:00 in no source: a gap
    assert body["gaps"] == [{"from": "2026-11-01T02:00:00+00:00", "to": "2026-11-01T03:00:00+00:00"}]
    assert [s["source"] for s in body["sources"]] == ["db", "archive", "db"]
    assert float(body["bars"][1]["c"]) == arch.loc["2026-11-01 01:00", "close"].item()
    f = body["forming"]
    assert f["closed"] is False and f["t"] == hour(6) and f["minutes"] == 30
    assert hour(6) not in ts


def test_forming_bar_is_not_used_for_indicators():
    now = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    m1 = _ohlc(60 * 6 + 30, "1min", "2026-11-01", seed=4)
    svc = _svc({}, now, _Store(minutes=m1))
    body = _run(svc.indicators("BTCUSDT", "spot", "1h", datetime(2026, 11, 1, tzinfo=UTC), None, "basic"))
    assert all(v["t"] < int(datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp()) for s in body["series"] for v in s["values"])


def test_daily_spot_prefers_db_and_perp_uses_the_archive():
    now = datetime(2026, 6, 10, 9, tzinfo=UTC)
    db = _ohlc(10, "1D", "2026-05-31", seed=6)
    arch = _ohlc(40, "1D", "2026-05-01", seed=7)
    svc = _svc({("spot", "1d"): arch, ("perp", "1d"): arch * 1.001}, now, _Store(daily=db))
    spot = _run(svc.bars("BTCUSDT", "spot", "1d", datetime(2026, 5, 20, tzinfo=UTC), None))
    assert [s["source"] for s in spot["sources"]] == ["archive", "db"]
    assert float(spot["bars"][-1]["c"]) == db["close"].iloc[-1]
    perp = _run(svc.bars("BTCUSDT", "perp", "1d", datetime(2026, 5, 20, tzinfo=UTC), None))
    assert perp["forming"] is None and float(perp["bars"][-1]["c"]) == (arch * 1.001).loc["2026-06-09", "close"].item()


def test_bad_requests():
    svc = _svc({}, datetime(2026, 11, 1, tzinfo=UTC))
    for args in [("DOGEUSDT", "spot", "1d"), ("BTCUSDT", "cfd", "1d"), ("BTCUSDT", "spot", "5m")]:
        with pytest.raises(BadRequest):
            _run(svc.bars(*args, None, None))
    with pytest.raises(BadRequest):
        _run(svc.bars("BTCUSDT", "spot", "1h", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)))
    with pytest.raises(BadRequest):
        _run(svc.indicators("BTCUSDT", "spot", "1d", None, None, "macd"))


def test_slow_archive_answers_loading_then_serves_from_cache():
    now = datetime(2026, 11, 1, 9, tzinfo=UTC)
    arch = _Archive({("perp", "1d"): _ohlc(60, "1D", "2026-09-01")})
    gate = {"slow": True}

    def loader(*a):
        if gate["slow"]:
            import time
            time.sleep(0.3)
        return arch(*a)

    svc = ChartService(_Store(), ArchiveCache(loader, clock=lambda: now, wait=0.05), clock=lambda: now)

    async def go():
        with pytest.raises(Loading):
            await svc.bars("BTCUSDT", "perp", "1d", datetime(2026, 9, 20, tzinfo=UTC), None)
        await asyncio.sleep(0.5)
        gate["slow"] = False
        return await svc.bars("BTCUSDT", "perp", "1d", datetime(2026, 9, 20, tzinfo=UTC), None)

    body = _run(go())
    assert len(body["bars"]) == 41 and body["gaps"] == [{"from": "2026-10-31T00:00:00+00:00", "to": "2026-11-01T00:00:00+00:00"}]


# --- endpoints ---------------------------------------------------------------------------------


def test_chart_endpoints_need_a_viewer_and_valid_parameters():
    now = datetime(2026, 11, 1, 9, tzinfo=UTC)
    svc = _svc({("perp", "1d"): _ohlc(400, "1D", "2025-10-28")}, now)
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), charts=svc)
    c = TestClient(app)
    ok = _get(c, "/api/v1/charts/BTCUSDT/bars?venue=perp&timeframe=1d&from=2026-10-01T00:00:00Z", _token(VIEWER))
    assert ok.status_code == 200 and len(ok.json()["bars"]) == 31
    ind = _get(c, "/api/v1/charts/BTCUSDT/indicators?venue=perp&timeframe=1d&set=r10a", _token(VIEWER))
    assert ind.status_code == 200 and {s["key"] for s in ind.json()["series"]} == {"sma100", "band_upper", "band_lower", "band_state"}
    assert _get(c, "/api/v1/charts/BTCUSDT/bars?timeframe=1m", _token(VIEWER)).status_code == 422
    assert _get(c, "/api/v1/charts/BTCUSDT/bars", _token(STRANGER)).status_code == 403
    assert _get(c, "/api/v1/charts/BTCUSDT/bars").status_code == 401
