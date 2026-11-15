"""Candlestick bars and server-side indicators for the chart page (docs/m2-charts-plan.md §4.2).

Bars are closed OHLCV bars, indexed by open time (UTC), from two sources:

- ``trading.market_bars`` (the worker's Spot Mainnet bars): 1d as stored; 1h from the stored 1m
  bars, only for hours that have all 60 minutes; the hour, 4h or day in progress is the
  ``forming`` bar, labelled ``closed: false`` and never used for indicators;
- data.binance.vision (checksum-verified, ``trading.alerts.live_data``) for anything older or
  incomplete in the database, and for the perpetual (the series R10a decides on).

4h bars are built from four present 1h bars (00/04/08/… UTC). Nothing is filled in: a bar no
source has is missing and listed in ``gaps``. Indicators are computed with
``trading.features.indicators`` (the functions the strategies use) on the regular bar grid with
NaN rows for missing bars, so a window that spans a gap is undefined, as in the strategies.

Prices and indicator values leave as strings (``repr`` of the float, so they round-trip exactly);
the browser converts them only to draw.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

from trading.features import indicators as ind

log = logging.getLogger(__name__)

SYMBOLS = ("BTCUSDT",)
VENUES = {"spot": "BINANCE_SPOT_MAINNET", "perp": "BINANCE_UM_PERP"}
STEP = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
MAX_BARS = 5_000
DEFAULT_BARS = 500
COLUMNS = ["open", "high", "low", "close", "volume"]

# Bars loaded before ``from`` so each indicator is settled at the first bar shown. R10a decides on
# the 400 daily closes before the day (strategies/r10a_v2.HISTORY_DAYS). An MT5 EMA is seeded at the
# first value it sees; after 2,100 bars the seed's weight in EMA200 is (199/201)^2100 < 1e-9.
WARMUP = {"r10a": 400, "e1": 2_100, "basic": 2_100}


class BadRequest(ValueError):
    pass


# --- indicator sets ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    key: str
    label: str
    panel: str                # "price" (on the candles) or "lower" (own panel)
    formula: str
    values: np.ndarray


def indicator_set(name: str, bars: pd.DataFrame) -> list[Line]:
    """Indicators of ``name`` on ``bars`` (a regular grid; missing bars are NaN rows)."""
    o = bars.astype(float)
    h, lo, c = o["high"].to_numpy(), o["low"].to_numpy(), o["close"].to_numpy()
    if name == "r10a":
        from trading.strategies.sma_band import BAND, SMA_N
        s = ind.sma(o["close"], SMA_N)
        state = ind.band_latch(o["close"], signed=False, sma_n=SMA_N, band=BAND).to_numpy()
        return [Line("sma100", "SMA100", "price", "sma(close, 100)", s),
                Line("band_upper", "SMA100 +2%", "price", "sma100 * 1.02 (เข้าเมื่อปิดเหนือ)", s * (1 + BAND)),
                Line("band_lower", "SMA100 −2%", "price", "sma100 * 0.98 (ออกเมื่อปิดใต้)", s * (1 - BAND)),
                Line("band_state", "R10a ถือ (1) / ไม่ถือ (0)", "lower", "band_latch(close, 100, 2%)", state)]
    if name == "e1":
        up, dn = ind.donchian(h, lo, 20)
        return [Line("ema50", "EMA50", "price", "ema_mt5(close, 50)", ind.ema_mt5(c, 50)),
                Line("ema200", "EMA200", "price", "ema_mt5(close, 200)", ind.ema_mt5(c, 200)),
                Line("donchian20_upper", "Donchian 20 บน", "price", "max(high, 20)", up),
                Line("donchian20_lower", "Donchian 20 ล่าง", "price", "min(low, 20)", dn),
                Line("adx14", "ADX14", "lower", "adx_mt5(14)", ind.adx_mt5(h, lo, c, 14))]
    if name == "basic":
        up20, dn20 = ind.donchian(h, lo, 20)
        up10, dn10 = ind.donchian(h, lo, 10)
        return [Line("ema20", "EMA20", "price", "ema_mt5(close, 20)", ind.ema_mt5(c, 20)),
                Line("ema50", "EMA50", "price", "ema_mt5(close, 50)", ind.ema_mt5(c, 50)),
                Line("ema200", "EMA200", "price", "ema_mt5(close, 200)", ind.ema_mt5(c, 200)),
                Line("donchian20_upper", "Donchian 20 บน", "price", "max(high, 20)", up20),
                Line("donchian20_lower", "Donchian 20 ล่าง", "price", "min(low, 20)", dn20),
                Line("donchian10_upper", "Donchian 10 บน", "price", "max(high, 10)", up10),
                Line("donchian10_lower", "Donchian 10 ล่าง", "price", "min(low, 10)", dn10),
                Line("atr14", "ATR14", "lower", "atr_wilder(14)", ind.atr_wilder(h, lo, c, 14))]
    raise BadRequest(f"unknown set {name!r}; allowed: {sorted(WARMUP)}")


# --- bar assembly (pure) ----------------------------------------------------------------------


def grid(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, step: pd.Timedelta) -> pd.DataFrame:
    """``bars`` on every open time in [start, end) (step-aligned from the epoch); missing = NaN."""
    first = start.ceil(step) if step < pd.Timedelta(days=1) else start.ceil("1D")
    idx = pd.date_range(first, end - step, freq=step) if end - step >= first else pd.DatetimeIndex([], tz="UTC")
    return bars.reindex(idx)[COLUMNS] if len(bars) else pd.DataFrame(index=idx, columns=COLUMNS, dtype=float)


def merge(primary: pd.DataFrame, fallback: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Primary bar where present, else fallback. Returns (bars, source per bar)."""
    p = primary[COLUMNS].dropna() if len(primary) else primary.reindex(columns=COLUMNS)
    f = fallback[COLUMNS].dropna() if len(fallback) else fallback.reindex(columns=COLUMNS)
    f = f[~f.index.isin(p.index)]
    out = pd.concat([p, f]).sort_index()
    src = pd.Series(["db"] * len(p) + ["archive"] * len(f), index=p.index.append(f.index)).sort_index()
    return out, src


def four_hour(h1: pd.DataFrame) -> pd.DataFrame:
    """4h bars from four present 1h bars; a partial group is missing."""
    h1 = h1[COLUMNS].dropna()
    if not len(h1):
        return h1
    g = h1.groupby(pd.DatetimeIndex(h1.index).floor("4h"))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].size()})
    return out[out["n"] == 4][COLUMNS]


def complete_hours(hourly: pd.DataFrame) -> pd.DataFrame:
    """Hours aggregated from 1m bars (column ``n`` = minutes present) that have all 60 minutes."""
    if not len(hourly):
        return hourly.reindex(columns=COLUMNS)
    return hourly[hourly["n"] == 60][COLUMNS]


def gaps(g: pd.DataFrame, step: pd.Timedelta) -> list[dict[str, str]]:
    """Runs of missing bars on the grid, as [from, to) open times."""
    out: list[dict[str, str]] = []
    miss = g["close"].isna().to_numpy()
    i = 0
    while i < len(miss):
        if miss[i]:
            j = i
            while j + 1 < len(miss) and miss[j + 1]:
                j += 1
            out.append({"from": g.index[i].isoformat(), "to": (g.index[j] + step).isoformat()})
            i = j + 1
        else:
            i += 1
    return out


def _s(x: Any) -> str | None:
    return None if x is None or not np.isfinite(x) else repr(float(x))


def _t(ts: pd.Timestamp) -> int:
    return int(ts.timestamp())


def bars_body(symbol: str, venue: str, timeframe: str, g: pd.DataFrame, src: pd.Series,
              forming: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    step = STEP[timeframe]
    rows = g.dropna(subset=["close"])
    sources: list[dict[str, Any]] = []
    for t in rows.index:
        s = src.get(t, "db")
        if sources and sources[-1]["source"] == s:
            sources[-1]["to"] = (t + step).isoformat()
        else:
            sources.append({"from": t.isoformat(), "to": (t + step).isoformat(), "source": s})
    return {"symbol": symbol, "venue": venue, "timeframe": timeframe,
            "bars": [{"t": _t(t), "o": _s(r.open), "h": _s(r.high), "l": _s(r.low), "c": _s(r.close), "v": _s(r.volume)}
                     for t, r in zip(rows.index, rows.itertuples(index=False), strict=True)],
            "forming": forming, "gaps": gaps(g, step), "sources": sources, "as_of": now.isoformat()}


def indicators_body(name: str, g: pd.DataFrame, shown_from: pd.Timestamp) -> dict[str, Any]:
    keep = g.index >= shown_from
    idx = g.index[keep]
    series = []
    for line in indicator_set(name, g):
        v = line.values[keep]
        series.append({"key": line.key, "label": line.label, "panel": line.panel, "formula": line.formula,
                       "values": [{"t": _t(t), "v": _s(x)} for t, x in zip(idx, v, strict=True)]})
    return {"set": name, "series": series}


# --- sources ----------------------------------------------------------------------------------


class BarStore(Protocol):
    async def daily(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame: ...
    async def hourly(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """1m bars aggregated per hour, with ``n`` = minutes present."""
        ...


class NoBars:
    async def daily(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame(columns=COLUMNS, dtype=float)

    async def hourly(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame(columns=[*COLUMNS, "n"], dtype=float)


DAILY_SQL = ("select open_time, open, high, low, close, volume from trading.market_bars "
             "where venue = %s and symbol = %s and interval = '1d' and open_time >= %s and open_time < %s order by open_time")
HOURLY_SQL = ("select date_bin('1 hour', open_time, timestamptz '1970-01-01') as h, "
              "(array_agg(open order by open_time))[1], max(high), min(low), (array_agg(close order by open_time desc))[1], "
              "sum(volume), count(*) from trading.market_bars "
              "where venue = %s and symbol = %s and interval = '1m' and open_time >= %s and open_time < %s group by 1 order by 1")


def _frame(rows: list[tuple[Any, ...]], cols: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=cols, dtype=float)
    df = pd.DataFrame([r[1:] for r in rows], columns=cols, index=pd.DatetimeIndex([r[0] for r in rows]).tz_convert("UTC"))
    return df.astype(float)


class PgBarStore:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def daily(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        async with self.pool.connection() as conn:
            rows = list(await (await conn.execute(DAILY_SQL, (venue, symbol, start, end))).fetchall())
        return _frame(rows, COLUMNS)

    async def hourly(self, venue: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        async with self.pool.connection() as conn:
            rows = list(await (await conn.execute(HOURLY_SQL, (venue, symbol, start, end))).fetchall())
        return _frame(rows, [*COLUMNS, "n"])


def _archive_month(venue: str, interval: str, symbol: str, y: int, m: int, today: date) -> pd.DataFrame:
    from trading.alerts import live_data
    start = date(y, m, 1)
    end = min((start + timedelta(days=32)).replace(day=1) - timedelta(days=1), today)
    fn = {("spot", "1d"): live_data.spot_daily_klines, ("spot", "1h"): live_data.spot_hourly_klines,
          ("perp", "1d"): live_data.daily_klines, ("perp", "1h"): live_data.hourly_klines}[(venue, interval)]
    return fn(symbol, start, end)


class ArchiveCache:
    """Archive bars per calendar month. Past months are kept; the current month is refreshed after
    ``ttl``. Downloads run in a thread; a request waits at most ``wait`` seconds and then gets
    :class:`Loading` while the download goes on."""

    def __init__(self, loader: Callable[[str, str, str, int, int, date], pd.DataFrame] = _archive_month,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 ttl: timedelta = timedelta(hours=1), wait: float = 8.0) -> None:
        self._load, self._now, self._ttl, self._wait = loader, clock, ttl, wait
        self._data: dict[tuple[str, str, str, int, int], tuple[datetime, pd.DataFrame]] = {}
        self._running: dict[tuple[str, str, str, int, int], asyncio.Task[None]] = {}

    async def _fetch(self, key: tuple[str, str, str, int, int]) -> None:
        try:
            df = await asyncio.to_thread(self._load, *key, self._now().date())
        except Exception:
            log.exception("archive download failed for %s", key)
        else:
            self._data[key] = (self._now(), df)
        finally:
            self._running.pop(key, None)

    async def bars(self, venue: str, interval: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        now = self._now()
        keys = []
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            keys.append((venue, interval, symbol, y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        current = (now.year, now.month)
        for k in keys:
            have = self._data.get(k)
            stale = have is not None and (k[3], k[4]) >= current and now - have[0] >= self._ttl
            if (have is None or stale) and k not in self._running:
                self._running[k] = asyncio.create_task(self._fetch(k))
        waiting = [self._running[k] for k in keys if k in self._running and k not in self._data]
        if waiting:
            done, pending = await asyncio.wait(waiting, timeout=self._wait)
            if pending:
                raise Loading()
        frames = [self._data[k][1] for k in keys if k in self._data]
        frames = [f for f in frames if len(f)]
        if not frames:
            return pd.DataFrame(columns=COLUMNS, dtype=float)
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        return df[(df.index >= utc(start)) & (df.index < utc(end))][COLUMNS]


class Loading(RuntimeError):
    pass


# --- the service ------------------------------------------------------------------------------


@dataclass
class Window:
    start: pd.Timestamp       # first bar shown
    end: pd.Timestamp         # exclusive; at most the open of the bar in progress
    load_from: pd.Timestamp   # first bar loaded (warm-up)


def utc(x: datetime) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def window(timeframe: str, start: datetime | None, end: datetime | None, now: datetime, warmup: int) -> Window:
    if timeframe not in STEP:
        raise BadRequest(f"unknown timeframe {timeframe!r}; allowed: {sorted(STEP)}")
    step = STEP[timeframe]
    current = utc(now).floor(step)
    e = min(utc(end).floor(step), current) if end else current
    s = utc(start).ceil(step) if start else e - DEFAULT_BARS * step
    if s >= e:
        raise BadRequest("from must be before to")
    if (e - s) / step > MAX_BARS:
        raise BadRequest(f"at most {MAX_BARS} bars per request")
    return Window(s, e, s - warmup * step)


class ChartService:
    def __init__(self, store: BarStore | None = None, archive: ArchiveCache | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.store: BarStore = store or NoBars()
        self.archive = archive or ArchiveCache(clock=clock)
        self.now = clock

    def _check(self, symbol: str, venue: str) -> None:
        if symbol not in SYMBOLS:
            raise BadRequest(f"unknown symbol {symbol!r}")
        if venue not in VENUES:
            raise BadRequest(f"unknown venue {venue!r}; allowed: {sorted(VENUES)}")

    async def _hourly(self, symbol: str, venue: str, a: pd.Timestamp, b: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
        db = complete_hours(await self.store.hourly(VENUES[venue], symbol, a, b)) if venue == "spot" else None
        arch = await self.archive.bars(venue, "1h", symbol, a, b)
        return merge(db if db is not None else arch.iloc[0:0], arch)

    async def closed(self, symbol: str, venue: str, timeframe: str, a: pd.Timestamp, b: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
        """Closed bars with open time in [a, b) on the regular grid, and the source of each."""
        self._check(symbol, venue)
        if timeframe == "1d":
            db = await self.store.daily(VENUES[venue], symbol, a, b) if venue == "spot" else pd.DataFrame(columns=COLUMNS)
            arch = await self.archive.bars(venue, "1d", symbol, a, b)
            bars, src = merge(db, arch)
        else:
            bars, src = await self._hourly(symbol, venue, a, b)
            if timeframe == "4h":
                bars = four_hour(bars)
                src = src.groupby(pd.DatetimeIndex(src.index).floor("4h")).agg(lambda s: "db" if (s == "db").all() else "archive")
        return grid(bars, a, b, STEP[timeframe]), src

    async def forming(self, symbol: str, venue: str, timeframe: str, w: Window) -> dict[str, Any] | None:
        """The bar in progress (Spot only, from the stored 1m bars); never used for indicators."""
        if venue != "spot" or w.end != window(timeframe, None, None, self.now(), 0).end:
            return None
        step = STEP[timeframe]
        hours = await self.store.hourly(VENUES[venue], symbol, w.end, w.end + step)
        if not len(hours):
            return None
        return {"t": _t(w.end), "o": _s(hours["open"].iloc[0]), "h": _s(hours["high"].max()), "l": _s(hours["low"].min()),
                "c": _s(hours["close"].iloc[-1]), "v": _s(hours["volume"].sum()), "closed": False,
                "minutes": int(hours["n"].sum())}

    async def bars(self, symbol: str, venue: str, timeframe: str, start: datetime | None, end: datetime | None) -> dict[str, Any]:
        self._check(symbol, venue)
        w = window(timeframe, start, end, self.now(), 0)
        g, src = await self.closed(symbol, venue, timeframe, w.start, w.end)
        return bars_body(symbol, venue, timeframe, g, src, await self.forming(symbol, venue, timeframe, w), self.now())

    async def indicators(self, symbol: str, venue: str, timeframe: str, start: datetime | None, end: datetime | None,
                         name: str) -> dict[str, Any]:
        self._check(symbol, venue)
        if name not in WARMUP:
            raise BadRequest(f"unknown set {name!r}; allowed: {sorted(WARMUP)}")
        w = window(timeframe, start, end, self.now(), WARMUP[name])
        g, _ = await self.closed(symbol, venue, timeframe, w.load_from, w.end)
        return {"symbol": symbol, "venue": venue, "timeframe": timeframe, **indicators_body(name, g, w.start)}
