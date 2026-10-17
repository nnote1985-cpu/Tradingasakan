"""Closed bars with gap, duplicate and revision handling (testnet-development-plan.md §4, §4.3; RT02, RT04, RT05).

Only closed bars are accepted: a stream kline with ``x`` false, or any bar
whose close time is not yet in the past, is refused, so an unclosed candle
can never feed a feature. ``BarSeries`` keeps the last accepted open time and
the gaps behind it. A bar that repeats one already held is a duplicate and
changes nothing; one with the same open time but different values is a
revision: the first value stays (decisions were frozen on it) and the
revision is counted for the operator. Bars older than the last one are only
taken when they fill a known gap, which is how backfill closes gaps.

``BarStore`` appends accepted bars to one JSONL file per symbol and interval
and rebuilds the series from it on restart, so a restart neither loses
history nor re-decides past bars.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

INTERVAL_MS = {"1m": 60_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class Outcome(StrEnum):
    NEW = "NEW"
    GAP_FILL = "GAP_FILL"
    DUPLICATE = "DUPLICATE"
    REVISION = "REVISION"
    STALE = "STALE"
    UNCLOSED = "UNCLOSED"
    MISALIGNED = "MISALIGNED"


@dataclass(frozen=True)
class Bar:
    symbol: str
    interval: str
    open_time: int          # ms since epoch, UTC
    close_time: int         # last ms of the bar, as Binance reports it
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str             # e.g. "mainnet_ws", "mainnet_rest", "agg_1m"

    def values(self) -> tuple[Decimal, ...]:
        return (self.open, self.high, self.low, self.close, self.volume)

    def to_json(self) -> dict[str, Any]:
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}

    @staticmethod
    def from_json(d: dict[str, Any]) -> Bar:
        return Bar(d["symbol"], d["interval"], int(d["open_time"]), int(d["close_time"]), Decimal(d["open"]), Decimal(d["high"]),
                   Decimal(d["low"]), Decimal(d["close"]), Decimal(d["volume"]), d["source"])


def bar_from_rest(symbol: str, interval: str, row: Sequence[Any], source: str = "mainnet_rest") -> Bar:
    """One row of GET /api/v3/klines: [open time, o, h, l, c, v, close time, ...]."""
    return Bar(symbol, interval, int(row[0]), int(row[6]), Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4]),
               Decimal(row[5]), source)


def bar_from_stream(msg: dict[str, Any], source: str = "mainnet_ws") -> Bar | None:
    """A kline stream event; None while the bar is still open (``k.x`` false)."""
    k = msg.get("k")
    if not isinstance(k, dict) or not k.get("x"):
        return None
    return Bar(str(k["s"]), str(k["i"]), int(k["t"]), int(k["T"]), Decimal(k["o"]), Decimal(k["h"]), Decimal(k["l"]),
               Decimal(k["c"]), Decimal(k["v"]), source)


@dataclass
class BarSeries:
    symbol: str
    interval: str
    last_open: int | None = None
    gaps: list[tuple[int, int]] = field(default_factory=list)      # open times [first, last] missing, inclusive
    bars: dict[int, Bar] = field(default_factory=dict)
    duplicates: int = 0
    revisions: int = 0

    @property
    def step(self) -> int:
        return INTERVAL_MS[self.interval]

    def accept(self, bar: Bar, now_ms: int) -> Outcome:
        if (bar.symbol, bar.interval) != (self.symbol, self.interval):
            raise ValueError(f"{bar.symbol} {bar.interval} does not belong to {self.symbol} {self.interval}")
        if bar.close_time >= now_ms:
            return Outcome.UNCLOSED
        if bar.open_time % self.step or bar.close_time != bar.open_time + self.step - 1:
            return Outcome.MISALIGNED
        held = self.bars.get(bar.open_time)
        if held is not None:
            if held.values() == bar.values():
                self.duplicates += 1
                return Outcome.DUPLICATE
            self.revisions += 1
            return Outcome.REVISION
        if self.last_open is None or bar.open_time > self.last_open:
            if self.last_open is not None and bar.open_time > self.last_open + self.step:
                self.gaps.append((self.last_open + self.step, bar.open_time - self.step))
            self.last_open = bar.open_time
            self.bars[bar.open_time] = bar
            return Outcome.NEW
        if self._fill(bar.open_time):
            self.bars[bar.open_time] = bar
            return Outcome.GAP_FILL
        return Outcome.STALE

    def _fill(self, t: int) -> bool:
        for i, (a, b) in enumerate(self.gaps):
            if a <= t <= b:
                parts = [(a, t - self.step), (t + self.step, b)]
                self.gaps[i:i + 1] = [(x, y) for x, y in parts if x <= y]
                return True
        return False

    def missing(self, now_ms: int, since: int | None = None) -> list[tuple[int, int]]:
        """Open-time ranges to backfill: the known gaps plus the tail from the last bar (or ``since``) to the last closed bar."""
        out = list(self.gaps)
        last_closed = now_ms // self.step * self.step - self.step
        start = self.last_open + self.step if self.last_open is not None else since
        if start is not None and start <= last_closed:
            out.append((start, last_closed))
        return out

    def closed(self, start: int | None = None, end: int | None = None) -> list[Bar]:
        return [self.bars[t] for t in sorted(self.bars) if (start is None or t >= start) and (end is None or t <= end)]


def aggregate(bars: Iterable[Bar], interval: str) -> list[Bar]:
    """Build ``interval`` bars from 1m bars; only buckets with every minute present are returned (§4.3 lineage)."""
    step = INTERVAL_MS[interval]
    per = step // INTERVAL_MS["1m"]
    buckets: dict[int, list[Bar]] = {}
    for b in bars:
        if b.interval != "1m":
            raise ValueError("aggregate takes 1m bars")
        buckets.setdefault(b.open_time - b.open_time % step, []).append(b)
    out = []
    for start in sorted(buckets):
        group = sorted(buckets[start], key=lambda b: b.open_time)
        if len(group) != per or len({b.open_time for b in group}) != per:
            continue
        out.append(Bar(group[0].symbol, interval, start, start + step - 1, group[0].open, max(b.high for b in group),
                       min(b.low for b in group), group[-1].close, sum((b.volume for b in group), Decimal(0)), "agg_1m"))
    return out


class BarStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, interval: str) -> Path:
        return self.root / f"{symbol}_{interval}.jsonl"

    def append(self, bar: Bar) -> None:
        with self._path(bar.symbol, bar.interval).open("a", encoding="utf-8") as f:
            f.write(json.dumps(bar.to_json()) + "\n")

    def load(self, symbol: str, interval: str, now_ms: int) -> BarSeries:
        """Rebuild a series from disk in open-time order; gaps between stored bars come back as gaps."""
        series = BarSeries(symbol, interval)
        p = self._path(symbol, interval)
        if p.exists():
            rows = [Bar.from_json(json.loads(line)) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
            for bar in sorted(rows, key=lambda b: b.open_time):
                series.accept(bar, now_ms)
            series.duplicates = series.revisions = 0
        return series
