"""Collector for the Binance public archive (data.binance.vision) into the raw store.

The REST API and websockets are not reachable from the cloud runtime
(docs/phase-2-data.md), so this reads daily archive files, which Binance
publishes about 8 hours after each UTC day closes. Each file is checked against
its published .CHECKSUM before anything is stored; ``received_at`` is taken when
the bytes have arrived, never before the request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from trading.data.raw_store import RawStore

FUTURES_BASE = "https://data.binance.vision/data/futures/um"
SPOT_BASE = "https://data.binance.vision/data/spot"


@dataclass(frozen=True)
class Dataset:
    name: str
    base: str
    kind: str              # archive folder: klines, premiumIndexKlines, fundingRate
    interval: str | None   # 1h for bar datasets, None for funding
    period: str            # "daily" or "monthly"

    def url(self, symbol: str, day: date) -> str:
        if self.period == "monthly":
            stem = f"{symbol}-{self.kind}-{day.year}-{day.month:02d}"
            return f"{self.base}/monthly/{self.kind}/{symbol}/{stem}.zip"
        return f"{self.base}/daily/{self.kind}/{symbol}/{self.interval}/{symbol}-{self.interval}-{day.isoformat()}.zip"


DATASETS = {
    d.name: d for d in (
        Dataset("perp_klines_1h", FUTURES_BASE, "klines", "1h", "daily"),
        Dataset("spot_klines_1h", SPOT_BASE, "klines", "1h", "daily"),
        Dataset("premium_index_1h", FUTURES_BASE, "premiumIndexKlines", "1h", "daily"),
        Dataset("funding", FUTURES_BASE, "fundingRate", None, "monthly"),
    )
}


class ChecksumMismatch(RuntimeError):
    pass


Fetch = Callable[[str], bytes | None]   # None means the file is not published (404)
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _default_fetch(url: str) -> bytes | None:
    from trading.market_data.binance_public import _get
    return _get(url)


class Collector:
    def __init__(self, store: RawStore, fetch: Fetch = _default_fetch, clock: Clock = _now):
        self.store, self.fetch, self.clock = store, fetch, clock

    def collect_file(self, dataset: Dataset, symbol: str, day: date) -> dict:
        url = dataset.url(symbol, day)
        blob = self.fetch(url)
        if blob is None:
            return {"source_key": url, "status": "MISSING"}
        ref = self.fetch(url + ".CHECKSUM")
        received_at = self.clock()
        if ref is None:
            return {"source_key": url, "status": "NO_CHECKSUM"}       # never stored unverified
        want = ref.decode().split()[0].lower()
        got = hashlib.sha256(blob).hexdigest()
        if got != want:
            raise ChecksumMismatch(f"{url}: sha256 {got} but published {want}")
        status, rec = self.store.put(url, blob, received_at, checksum="VERIFIED",
                                     meta={"dataset": dataset.name, "symbol": symbol, "period": dataset.period,
                                           "day": day.isoformat()},
                                     recorded_at=self.clock())
        return {"source_key": url, "status": status, "revision": rec.revision, "sha256": rec.sha256}

    def collect(self, symbol: str, start: date, end: date, datasets: list[str] | None = None) -> list[dict]:
        """Every file for ``start``..``end`` inclusive. Monthly datasets are
        fetched once per month that the range touches."""
        out = []
        for name in datasets or list(DATASETS):
            ds = DATASETS[name]
            seen = set()
            d = start
            while d <= end:
                key = (d.year, d.month) if ds.period == "monthly" else d
                if key not in seen:
                    seen.add(key)
                    out.append({"dataset": name, **self.collect_file(ds, symbol, d)})
                d += timedelta(days=1)
        return out
