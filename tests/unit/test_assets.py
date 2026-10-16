import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from test_api_auth import STRANGER, URL, VIEWER, _get, _Jwks, _Roles, _token

from api.assets import AssetCache, asset_view, four_hour, trend
from api.auth import SupabaseJwtVerifier
from api.main import create_app
from trading.config import Settings
from trading.contracts.enums import OperatorRole


def _bars(closes, freq="1D", start="2025-09-01"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}, index=idx)


def test_trend_reads_close_against_ema20_and_ema50():
    assert trend(_bars(np.linspace(100, 200, 60)), "1d")["state"] == "up"
    assert trend(_bars(np.linspace(200, 100, 60)), "1d")["state"] == "down"
    zig = trend(_bars(list(np.linspace(100, 200, 59)) + [150]), "1d")
    assert zig["state"] == "mixed" and zig["close"] == "150.00"
    short = trend(_bars(np.linspace(100, 200, 30)), "1d")
    assert short["state"] is None and short["ema50"] is None and short["ema20"] is not None


def test_four_hour_bars_need_all_four_hours():
    h = _bars(range(1, 11), freq="1h", start="2026-10-14 00:00")
    b = four_hour(h)
    assert list(b["close"]) == [4.0, 8.0]          # hours 8-9 are an incomplete group
    assert b["high"].iloc[0] == 4 * 1.01


def test_asset_view_matches_the_risk_check_and_flags_stale_data():
    daily = _bars(np.linspace(50_000, 80_000, 400), start="2025-09-01")
    hourly = _bars(np.linspace(79_000, 80_000, 24 * 12), freq="1h", start=str(daily.index[-12].date()))
    funding = pd.Series(0.0003, index=daily.index[-40:])
    last = daily.index[-1].date()
    v = asset_view("BTCUSDT", daily, hourly, funding, False, None, last)
    assert v["available"] and not v["stale"] and v["data_through"] == str(last)
    assert v["close"] == "80000.00" and v["regime"]["trend_up"] is True
    assert v["funding"] == {"per_8h_7d": "0.000100", "hot": False, "estimated": False}
    assert [t["timeframe"] for t in v["timeframes"]] == ["1h", "4h", "1d"]
    assert all(t["state"] == "up" for t in v["timeframes"])
    assert v["drawdown_365d"] == "0.000000" and v["open_interest"] is None
    assert v["change"]["d1"] == str(round(80_000 / daily["close"].iloc[-2] - 1, 6))
    assert asset_view("BTCUSDT", daily, hourly, funding, False, None, last + timedelta(days=1))["stale"]


def test_cache_answers_at_once_and_refreshes_in_the_background():
    now = [datetime(2026, 10, 16, 9, tzinfo=UTC)]
    calls = []

    def loader(symbol):
        calls.append(symbol)
        if len(calls) == 2:
            raise RuntimeError("archive down")
        return {"available": True, "close": str(len(calls))}

    cache = AssetCache(loader, clock=lambda: now[0])

    async def run():
        first = await cache.get("BTCUSDT")
        assert first["available"] is False and first["loading"] is True and first["symbol"] == "BTCUSDT"
        await asyncio.sleep(0.05)
        ready = await cache.get("BTCUSDT")
        assert ready["close"] == "1" and ready["refreshing"] is False
        now[0] += timedelta(hours=2)
        stale = await cache.get("BTCUSDT")          # old figures, refresh started
        assert stale["close"] == "1" and stale["refreshing"] is True
        await asyncio.sleep(0.05)
        kept = await cache.get("BTCUSDT")           # refresh failed: keep old figures, wait before retrying
        assert kept["close"] == "1" and kept["refreshing"] is False and len(calls) == 2
        now[0] += timedelta(minutes=6)
        await cache.get("BTCUSDT")
        await asyncio.sleep(0.05)
        assert (await cache.get("BTCUSDT"))["close"] == "3"

    asyncio.run(run())


class _FixedAssets(AssetCache):
    async def get(self, symbol):
        return {"available": True, "symbol": symbol, "close": "80000.00"}


def test_asset_endpoints_need_a_viewer_and_a_known_symbol():
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}),
                     assets=_FixedAssets())
    c = TestClient(app)
    assert _get(c, "/api/v1/assets", _token(VIEWER)).json()["items"][0]["symbol"] == "BTCUSDT"
    assert _get(c, "/api/v1/assets/BTCUSDT", _token(VIEWER)).json()["close"] == "80000.00"
    assert _get(c, "/api/v1/assets/DOGEUSDT", _token(VIEWER)).status_code == 404
    assert _get(c, "/api/v1/assets/BTCUSDT", _token(STRANGER)).status_code == 403
