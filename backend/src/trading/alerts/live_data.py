"""Recent BTCUSDT data for the daily risk check, from data.binance.vision only.

The Binance REST API refuses this environment (HTTP 451), so everything comes
from the public archive, which publishes each UTC day about 8 hours after it
closes. Monthly files are used where they exist, daily files otherwise.

Funding prints for the current month are not in the archive until the month
ends; they are estimated from 1h premium-index klines with Binance's formula
F = P + clamp(I - P, -0.05%, +0.05%), I = 0.01% per 8h. Checked against
actual prints for 2025-09: mean absolute error 0.002% per print.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading.market_data.binance_public import BASE, _get, parse_funding_csv, parse_klines_csv

SPOT_BASE = "https://data.binance.vision/data/spot"
INTEREST_8H = 0.0001
CLAMP = 0.0005


class ChecksumError(RuntimeError):
    pass


def _get_verified(url: str) -> bytes | None:
    """Archive file, checked against its published .CHECKSUM (sha256)."""
    blob = _get(url)
    if blob is None:
        return None
    ref = _get(url + ".CHECKSUM")
    if ref is None:
        raise ChecksumError(f"no checksum published for {url}")
    want = ref.decode().split()[0].lower()
    if hashlib.sha256(blob).hexdigest() != want:
        raise ChecksumError(f"checksum mismatch for {url}")
    return blob


def _unzip(blob: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0])


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _month_days(y: int, m: int, end: date):
    d = date(y, m, 1)
    while d.month == m and d <= end:
        yield d
        d += timedelta(days=1)


def _fetch(kind: str, symbol: str, interval: str | None, start: date, end: date,
           base: str = BASE) -> list[bytes]:
    """CSV payloads for [start, end], monthly file when published else daily files."""
    sub = f"{kind}/{symbol}/{interval}" if interval else f"{kind}/{symbol}"
    stem = f"{symbol}-{interval}" if interval else f"{symbol}-{kind}"
    out = []
    for y, m in _months(start, end):
        blob = _get_verified(f"{base}/monthly/{sub}/{stem}-{y}-{m:02d}.zip")
        if blob is not None:
            out.append(_unzip(blob))
            continue
        if kind == "fundingRate":
            continue                          # no daily funding files exist
        for d in _month_days(y, m, end):
            blob = _get_verified(f"{base}/daily/{sub}/{stem}-{d.isoformat()}.zip")
            if blob is not None:
                out.append(_unzip(blob))
    return out


def clip(obj, start: date, end: date):
    """Rows whose timestamp falls on a UTC day in [start, end]; monthly files hold the whole month."""
    lo = pd.Timestamp(start, tz="UTC")
    hi = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    return obj[(obj.index >= lo) & (obj.index < hi)]


def _klines(symbol: str, interval: str, start: date, end: date, base: str = BASE) -> pd.DataFrame:
    frames = [parse_klines_csv(raw) for raw in _fetch("klines", symbol, interval, start, end, base)]
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
    df = pd.concat(frames).sort_index()
    return clip(df[~df.index.duplicated(keep="first")], start, end)


def daily_klines(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _klines(symbol, "1d", start, end)


def hourly_klines(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _klines(symbol, "1h", start, end)


def spot_daily_klines(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _klines(symbol, "1d", start, end, SPOT_BASE)


def spot_hourly_klines(symbol: str, start: date, end: date) -> pd.DataFrame:
    return _klines(symbol, "1h", start, end, SPOT_BASE)


def missing_days(index: pd.DatetimeIndex, start: date, end: date) -> list[str]:
    """UTC days in [start, end] with no row in ``index``."""
    have = set(index.floor("1D"))
    days = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="1D")
    return [str(d.date()) for d in days if d not in have]


def estimate_funding(premium_1h: pd.Series) -> pd.Series:
    """Estimated funding print at each 8h boundary from hourly premium index closes."""
    p = premium_1h.groupby(premium_1h.index.floor("8h")).mean()
    est = p + np.clip(INTEREST_8H - p, -CLAMP, CLAMP)
    est.index = est.index + pd.Timedelta(hours=8)
    return est


def daily_funding(symbol: str, start: date, end: date) -> tuple[pd.Series, pd.Series]:
    """(sum of funding prints per UTC day, True where the day used estimates)."""
    actual = [parse_funding_csv(raw) for raw in _fetch("fundingRate", symbol, None, start, end)]
    f = pd.concat(actual).sort_index() if actual else pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    f.index = f.index.round("1h")
    f = clip(f, start, end)
    last = f.index.max() if len(f) else pd.Timestamp(start, tz="UTC")
    est_start = max(start, (last + pd.Timedelta(hours=1)).date())
    estimated = pd.Series(dtype=float)
    if est_start <= end:
        prem = [parse_klines_csv(raw)["close"] for raw in _fetch("premiumIndexKlines", symbol, "1h", est_start, end)]
        if prem:
            p = clip(pd.concat(prem).sort_index(), est_start, end)
            estimated = estimate_funding(p[~p.index.duplicated()])
            estimated = clip(estimated[estimated.index > last], start, end)
    allp = pd.concat([f, estimated]).sort_index()
    # A print at 00:00 settles the previous day's last interval; keep it on its own date (as in backtests).
    daily = allp.groupby(allp.index.floor("1D")).sum()
    is_est = pd.Series(False, index=daily.index)
    if len(estimated):
        is_est.loc[estimated.index.floor("1D").unique().intersection(daily.index)] = True
    return daily, is_est


def open_interest(symbol: str, start: date, end: date) -> pd.Series:
    """Daily last open-interest value (USDT) from the archive's metrics files."""
    vals = []
    d = start
    while d <= end:
        blob = _get_verified(f"{BASE}/daily/metrics/{symbol}/{symbol}-metrics-{d.isoformat()}.zip")
        if blob is not None:
            m = pd.read_csv(io.BytesIO(_unzip(blob)))
            vals.append((pd.Timestamp(d, tz="UTC"), float(m["sum_open_interest_value"].iloc[-1])))
        d += timedelta(days=1)
    return pd.Series(dict(vals), dtype=float)
