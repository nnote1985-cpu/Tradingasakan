"""Collect Binance order book snapshots for later research (docs/orderbook-collection.md).

    python scripts/orderbook_collect.py --out /var/lib/tradingasakan-orderbook [--interval 10] [--levels 20]

Every --interval seconds (aligned to the clock) it fetches the top --levels of the
BTCUSDT book from Binance Spot (/api/v3/depth) and USD-M Futures (/fapi/v1/depth)
over public REST, no keys. Each snapshot is one JSON line:

    {"t": 1763424000000, "lt": 1763424000123, "u": 123, "mid": 91234.5, "spr_bps": 0.01,
     "imb5": 0.12, "imb20": -0.03, "b": [[price, qty], ...], "a": [[price, qty], ...]}

t = the slot time (ms, UTC), lt = local receive time, u = lastUpdateId,
imbN = (bid qty - ask qty) / (bid qty + ask qty) over the top N levels.
Files: <out>/<market>/<SYMBOL>/<YYYY-MM-DD>.jsonl for the current UTC day; the
previous days are gzipped to .jsonl.gz at rollover and on start. A failed fetch
writes nothing, so gaps show as missing slots. Writing pauses while free disk is
below --min-free-gb. Standard library only, so it never touches the workers' env.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import signal
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENDPOINTS = {
    "spot": "https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}",
    "perp": "https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={limit}",
}
# Limits both depth endpoints accept; fetch the smallest that covers --levels (request weight stays at 2-5).
API_LIMITS = (5, 10, 20, 50, 100)

UTC = timezone.utc  # noqa: UP017 (the VM runs this with Ubuntu 22.04 python3.10, which has no datetime.UTC)

Fetcher = Callable[[str], dict[str, Any]]


def http_fetch(url: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingasakan-orderbook/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https hosts)
        return json.loads(r.read())  # type: ignore[no-any-return]


def api_limit(levels: int) -> int:
    return next((n for n in API_LIMITS if n >= levels), API_LIMITS[-1])


def _imbalance(bids: list[list[float]], asks: list[list[float]], n: int) -> float | None:
    bq = sum(q for _, q in bids[:n])
    aq = sum(q for _, q in asks[:n])
    return round((bq - aq) / (bq + aq), 4) if bq + aq > 0 else None


def build_record(raw: dict[str, Any], slot_ms: int, recv_ms: int, levels: int) -> dict[str, Any] | None:
    """Turn a depth response into one compact line; None when the book is empty or crossed."""
    bids = [[float(p), float(q)] for p, q in raw.get("bids", [])[:levels]]
    asks = [[float(p), float(q)] for p, q in raw.get("asks", [])[:levels]]
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        return None
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    return {
        "t": slot_ms,
        "lt": recv_ms,
        "u": raw.get("lastUpdateId"),
        "mid": round(mid, 4),
        "spr_bps": round((ba - bb) / mid * 1e4, 4),
        "imb5": _imbalance(bids, asks, 5),
        "imb20": _imbalance(bids, asks, 20),
        "b": bids,
        "a": asks,
    }


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def compress_old(folder: Path, today: str) -> list[Path]:
    """Gzip every <day>.jsonl older than today; the .jsonl is removed only after the .gz is complete."""
    done = []
    for f in sorted(folder.glob("*.jsonl")):
        if f.stem >= today:
            continue
        gz = f.with_suffix(".jsonl.gz")
        tmp = gz.with_suffix(".gz.tmp")
        with f.open("rb") as src, gzip.open(tmp, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
        if gz.exists():  # a day that was split by a restart mid-compress: keep both parts
            gz = gz.with_name(f"{f.stem}.{int(time.time())}.jsonl.gz")
        tmp.rename(gz)
        f.unlink()
        done.append(gz)
    return done


@dataclass
class Collector:
    out: Path
    symbol: str = "BTCUSDT"
    markets: tuple[str, ...] = ("spot", "perp")
    levels: int = 20
    min_free_gb: float = 2.0
    fetch: Fetcher = http_fetch

    def folder(self, market: str) -> Path:
        p = self.out / market / self.symbol
        p.mkdir(parents=True, exist_ok=True)
        return p

    def rollover(self, today: str) -> None:
        for m in self.markets:
            for gz in compress_old(self.folder(m), today):
                log(f"compressed {gz}")

    def disk_ok(self) -> bool:
        return shutil.disk_usage(self.out).free >= self.min_free_gb * 1e9

    def collect_slot(self, slot_ms: int) -> dict[str, str]:
        """Fetch and append one snapshot per market; returns market -> ok/empty/error text."""
        status: dict[str, str] = {}
        day = day_of(slot_ms)
        for m in self.markets:
            url = ENDPOINTS[m].format(symbol=self.symbol, limit=api_limit(self.levels))
            try:
                raw = self.fetch(url)
            except Exception as e:  # network, HTTP 429/451/5xx, bad JSON: skip this slot
                status[m] = f"error {type(e).__name__}: {e}"[:200]
                continue
            rec = build_record(raw, slot_ms, int(time.time() * 1000), self.levels)
            if rec is None:
                status[m] = "empty"
                continue
            with (self.folder(m) / f"{day}.jsonl").open("a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            status[m] = "ok"
        return status


def log(msg: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {msg}", flush=True)


def run(c: Collector, interval: int, max_slots: int | None = None) -> None:
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    c.out.mkdir(parents=True, exist_ok=True)
    day = day_of(int(time.time() * 1000))
    c.rollover(day)
    log(f"start symbol={c.symbol} markets={','.join(c.markets)} levels={c.levels} interval={interval}s out={c.out}")
    n = 0
    errors = 0
    paused = False
    while not stop and (max_slots is None or n < max_slots):
        now = time.time()
        slot = (int(now) // interval + 1) * interval
        while not stop and time.time() < slot:
            time.sleep(min(0.5, slot - time.time()))
        if stop:
            break
        slot_ms = slot * 1000
        today = day_of(slot_ms)
        if today != day:
            c.rollover(today)
            day = today
        if not c.disk_ok():
            if not paused:
                log(f"PAUSED: free disk below {c.min_free_gb} GB")
            paused = True
            continue
        if paused:
            log("resumed: disk space back")
            paused = False
        st = c.collect_slot(slot_ms)
        n += 1
        bad = {m: s for m, s in st.items() if s != "ok"}
        if bad:
            errors += 1
            if errors <= 5 or errors % 100 == 0:  # don't flood the journal during an outage
                log(f"slot {slot} {bad} (errors so far {errors})")
        if n % (3600 // interval or 1) == 0:
            log(f"alive: {n} slots, {errors} with errors")
    log("stopped")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--markets", default="spot,perp")
    ap.add_argument("--interval", type=int, default=10, help="seconds between snapshots")
    ap.add_argument("--levels", type=int, default=20)
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--once", action="store_true", help="take one snapshot now, print status, exit")
    a = ap.parse_args()
    markets = tuple(m.strip() for m in a.markets.split(",") if m.strip())
    unknown = [m for m in markets if m not in ENDPOINTS]
    if unknown or a.interval < 1 or a.levels < 1:
        print(f"bad arguments: unknown markets {unknown}" if unknown else "interval and levels must be >= 1", file=sys.stderr)
        return 2
    c = Collector(a.out, a.symbol.upper(), markets, a.levels, a.min_free_gb)
    if a.once:
        st = c.collect_slot(int(time.time()) * 1000)
        print(json.dumps(st))
        return 0 if all(s == "ok" for s in st.values()) else 1
    run(c, a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
