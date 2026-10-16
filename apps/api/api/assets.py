"""Per-asset market status for the web: information to follow, not a trade signal.

Everything is computed from the closed daily and hourly bars on data.binance.vision, the same
source and the same rules as the daily risk check (trading.alerts). The archive publishes a UTC
day about 8 hours after it closes, so these figures run a day behind; the live price on the page
comes from the browser's own Binance stream and never reaches this module.

Downloading takes tens of seconds, longer than the browser waits, so results are cached per
symbol and refreshed in the background. Ratios and prices leave as decimal strings; the browser
only formats them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from trading.alerts.risk_check import signals, summary
from trading.features.indicators import ema

log = logging.getLogger(__name__)

# Symbols the page can show. Adding one needs only an entry here (the archive has the same files
# for every USDⓈ-M pair); the daily jobs and books stay BTC-only until a plan says otherwise.
ASSETS: dict[str, dict[str, str]] = {
    "BTCUSDT": {"symbol": "BTCUSDT", "base": "BTC", "name": "Bitcoin", "live_stream": "btcusdt@miniTicker"},
}

TREND_FAST, TREND_SLOW = 20, 50
PRICE = Decimal("0.01")
RATIO = Decimal("0.000001")
TTL = timedelta(hours=1)
RETRY_AFTER_ERROR = timedelta(minutes=5)


def _dec(x: Any, q: Decimal) -> str | None:
    if x is None or pd.isna(x):
        return None
    return str(Decimal(repr(float(x))).quantize(q))


def _price(x: Any) -> str | None:
    return _dec(x, PRICE)


def _ratio(x: Any) -> str | None:
    return _dec(x, RATIO)


def trend(bars: pd.DataFrame, timeframe: str) -> dict[str, Any]:
    """Close against EMA20 and EMA50 of the closed bars: up when close > EMA20 > EMA50, down when
    close < EMA20 < EMA50, mixed otherwise, None while either average is undefined."""
    close = bars["close"].to_numpy(dtype=float)
    out: dict[str, Any] = {"timeframe": timeframe, "bar_open": None, "close": None,
                           "ema20": None, "ema50": None, "state": None}
    if len(close) == 0:
        return out
    fast, slow = ema(close, TREND_FAST)[-1], ema(close, TREND_SLOW)[-1]
    c = close[-1]
    state = None
    if not (np.isnan(fast) or np.isnan(slow)):
        state = "up" if c > fast > slow else "down" if c < fast < slow else "mixed"
    out.update(bar_open=bars.index[-1].isoformat(), close=_price(c), ema20=_price(fast), ema50=_price(slow), state=state)
    return out


def four_hour(hourly: pd.DataFrame) -> pd.DataFrame:
    """4h bars (00/04/08/… UTC) from four present 1h bars; a partial group is dropped."""
    idx = pd.DatetimeIndex(hourly.index)
    g = hourly.groupby(idx.floor("4h"))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "n": g["close"].size()})
    return out[out["n"] == 4].drop(columns="n")


def _change(close: pd.Series, days: int) -> str | None:
    if len(close) <= days:
        return None
    return _ratio(close.iloc[-1] / close.iloc[-1 - days] - 1)


def asset_view(symbol: str, daily: pd.DataFrame, hourly: pd.DataFrame, funding_daily: pd.Series,
               funding_estimated: bool, oi: pd.Series | None, expected_through: date) -> dict[str, Any]:
    """The page's figures from closed bars. ``expected_through`` is the last UTC day the archive
    should have; anything older is flagged stale."""
    sig = signals(daily, funding_daily, oi)
    s = summary(sig, funding_estimated)
    last = sig.index[-1].date()
    close = sig["close"].dropna()
    month = daily.loc[daily.index > daily.index[-1] - pd.Timedelta(days=30)]
    return {
        "available": True,
        **ASSETS[symbol],
        "data_through": str(last),
        "stale": last < expected_through,
        "close": _price(s["close"]),
        "change": {"d1": _change(close, 1), "d7": _change(close, 7), "d30": _change(close, 30)},
        "range_30d": {"high": _price(month["high"].max()), "low": _price(month["low"].min())},
        "regime": {   # the SMA100 line every spot book and the risk check use
            "sma100": _price(s["sma100"]), "dist_to_sma": _ratio(s["dist_to_sma"]), "trend_up": s["trend_up"],
        },
        "timeframes": [trend(hourly, "1h"), trend(four_hour(hourly), "4h"), trend(daily, "1d")],
        "funding": {"per_8h_7d": _ratio(s["funding_7d_per_8h"]), "hot": s["funding_hot"], "estimated": s["funding_estimated"]},
        "vol_30d": _ratio(s["vol_30d"]),
        "drawdown_365d": _ratio(s["drawdown_365d"]),
        "open_interest": None if "oi_usdt" not in s else {"usdt": _price(s["oi_usdt"]), "change_7d": _ratio(s["oi_7d_change"])},
        "events": s["events"],
        "missing_inputs": s["missing_inputs"],
    }


def load_asset(symbol: str, today: date | None = None) -> dict[str, Any]:
    """Download what the page needs from data.binance.vision (perpetual bars, as in the risk check)."""
    from trading.alerts.live_data import daily_funding, daily_klines, hourly_klines, open_interest

    end = (today or datetime.now(UTC).date()) - timedelta(days=1)
    daily = daily_klines(symbol, end - timedelta(days=400), end)
    hourly = hourly_klines(symbol, end - timedelta(days=12), end)
    funding, est = daily_funding(symbol, end - timedelta(days=40), end)
    oi = open_interest(symbol, end - timedelta(days=8), end)
    last = daily.index[-1]
    return asset_view(symbol, daily, hourly, funding, bool(est.loc[est.index > last - timedelta(days=7)].any()), oi, end)


class AssetCache:
    """Answers at once from the last result and refreshes in the background when it is older than
    the TTL, so a request never waits on the download."""

    def __init__(self, loader: Callable[[str], dict[str, Any]] = load_asset,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._load, self._now = loader, clock
        self._data: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._failed: dict[str, datetime] = {}
        self._running: dict[str, asyncio.Task[None]] = {}

    async def _refresh(self, symbol: str) -> None:
        try:
            result = await asyncio.to_thread(self._load, symbol)
        except Exception:
            log.exception("asset refresh failed for %s", symbol)
            self._failed[symbol] = self._now()
        else:
            self._data[symbol] = (self._now(), result)
            self._failed.pop(symbol, None)
        finally:
            self._running.pop(symbol, None)

    async def get(self, symbol: str) -> dict[str, Any]:
        now = self._now()
        have = self._data.get(symbol)
        failed = self._failed.get(symbol)
        due = have is None or now - have[0] >= TTL
        if due and symbol not in self._running and (failed is None or now - failed >= RETRY_AFTER_ERROR):
            self._running[symbol] = asyncio.create_task(self._refresh(symbol))
        refreshing = symbol in self._running
        if have:
            return {**have[1], "loading": False, "refreshing": refreshing, "fetched_at": have[0].isoformat()}
        return {"available": False, **ASSETS[symbol], "loading": refreshing, "failed": failed is not None and not refreshing}
