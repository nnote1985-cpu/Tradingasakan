"""Read-only Binance mainnet data for the analysts' tools (docs/agent-book-plan.md §4, step 2).

Public endpoints only: no key, no account, nothing that can place an order.
The cloud gets HTTP 451 from Binance, so this runs where the worker runs.
Every function returns a small JSON-safe dict; prices stay strings so nothing
passes through float. ``get_json`` is injectable for tests.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from trading.agent.signals import rsi
from trading.ingest.bars import bar_from_rest

SPOT = "https://api.binance.com"
FUTURES = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
INTERVALS = ("1h", "4h", "1d")
MAX_CANDLES = 200

GetJson = Callable[[str], Any]


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def http_get_json(url: str, timeout: float = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingasakan-agent/1"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read())


def _sma(values: list[Decimal], n: int) -> str | None:
    return str((sum(values[-n:], Decimal(0)) / n).quantize(Decimal("0.01"))) if len(values) >= n else None


def candles(get_json: GetJson, interval: str, limit: int, now_ms: int) -> dict[str, Any]:
    """Closed candles, oldest first, with a few indicators computed by code so the model need not."""
    if interval not in INTERVALS or not 1 <= limit <= MAX_CANDLES:
        raise ValueError(f"interval must be one of {INTERVALS} and limit 1..{MAX_CANDLES}")
    rows = get_json(f"{SPOT}/api/v3/klines?symbol={SYMBOL}&interval={interval}&limit={limit + 1}")
    bars = [b for b in (bar_from_rest(SYMBOL, interval, r) for r in rows) if b.close_time < now_ms][-limit:]   # closed only
    closes = [b.close for b in bars]
    r = rsi(closes)
    return {
        "symbol": SYMBOL, "interval": interval, "columns": ["open_time_ms", "open", "high", "low", "close", "volume"],
        "candles": [[b.open_time, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)] for b in bars],
        "indicators": {"rsi14": None if r is None else str(r.quantize(Decimal("0.1"))),
                       "sma20": _sma(closes, 20), "sma50": _sma(closes, 50), "sma100": _sma(closes, 100)},
    }


def order_book(get_json: GetJson, limit: int) -> dict[str, Any]:
    """Spot depth summarised: spread and resting size within 0.5 % and 1 % of mid, plus the top 10 levels."""
    if limit not in (20, 50, 100, 500):
        raise ValueError("limit must be 20, 50, 100 or 500")
    d = get_json(f"{SPOT}/api/v3/depth?symbol={SYMBOL}&limit={limit}")
    bids = [(Decimal(p), Decimal(q)) for p, q in d["bids"]]
    asks = [(Decimal(p), Decimal(q)) for p, q in d["asks"]]
    mid = (bids[0][0] + asks[0][0]) / 2

    def within(side: list[tuple[Decimal, Decimal]], pct: str) -> str:
        band = mid * Decimal(pct) / 100
        return str(sum((q for p, q in side if abs(p - mid) <= band), Decimal(0)))

    return {
        "symbol": SYMBOL, "best_bid": str(bids[0][0]), "best_ask": str(asks[0][0]),
        "spread_bps": str(((asks[0][0] - bids[0][0]) / mid * 10_000).quantize(Decimal("0.01"))),
        "bid_btc_within_0_5pct": within(bids, "0.5"), "ask_btc_within_0_5pct": within(asks, "0.5"),
        "bid_btc_within_1pct": within(bids, "1"), "ask_btc_within_1pct": within(asks, "1"),
        "top_bids": [[str(p), str(q)] for p, q in bids[:10]], "top_asks": [[str(p), str(q)] for p, q in asks[:10]],
    }


def derivatives(get_json: GetJson) -> dict[str, Any]:
    """Perpetual funding, mark/index and open interest now and over the last 24 h."""
    p = get_json(f"{FUTURES}/fapi/v1/premiumIndex?symbol={SYMBOL}")
    oi = get_json(f"{FUTURES}/fapi/v1/openInterest?symbol={SYMBOL}")
    hist = get_json(f"{FUTURES}/futures/data/openInterestHist?symbol={SYMBOL}&period=1h&limit=25")
    first = Decimal(hist[0]["sumOpenInterest"]) if hist else None
    now = Decimal(oi["openInterest"])
    return {
        "symbol": SYMBOL, "mark_price": p["markPrice"], "index_price": p["indexPrice"],
        "last_funding_rate": p["lastFundingRate"], "next_funding_time_ms": p["nextFundingTime"],
        "open_interest_btc": str(now),
        "open_interest_change_24h_pct": None if not first else str(((now / first - 1) * 100).quantize(Decimal("0.01"))),
    }


def ticker_24h(get_json: GetJson) -> dict[str, Any]:
    t = get_json(f"{SPOT}/api/v3/ticker/24hr?symbol={SYMBOL}")
    keys = ("lastPrice", "priceChangePercent", "highPrice", "lowPrice", "volume", "quoteVolume", "count")
    return {"symbol": SYMBOL, **{k: t[k] for k in keys}}


def funding_rate(get_json: GetJson) -> Decimal:
    """The latest funding rate, for funding_above/below signals."""
    return Decimal(get_json(f"{FUTURES}/fapi/v1/premiumIndex?symbol={SYMBOL}")["lastFundingRate"])
