"""Download free BTCUSDT USD-M futures order-flow data from data.binance.vision (order book prototype).

    uv run python research/orderflow/download.py --start 2026-11-01 --end 2026-11-16

Sets: aggTrades (every taker trade, side known -> order flow / CVD), bookDepth (book depth
summed within +-1..5% of mid, ~every 30-60 s -> coarse imbalance) and 1m klines (bars).
Each zip is checked against its .CHECKSUM. Output: data/orderflow/<set>/<file>.zip
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um/daily"
OUT = Path("data/orderflow")
SETS = {
    "aggTrades": "aggTrades/{s}/{s}-aggTrades-{d}.zip",
    "bookDepth": "bookDepth/{s}/{s}-bookDepth-{d}.zip",
    "klines_1m": "klines/{s}/1m/{s}-1m-{d}.zip",
}


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (fixed https host)
            return bytes(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch(kind: str, sym: str, day: date) -> str:
    rel = SETS[kind].format(s=sym, d=day.isoformat())
    path = OUT / kind / Path(rel).name
    if path.exists():
        return f"{path.name} cached"
    body = _get(f"{BASE}/{rel}")
    if body is None:
        return f"{path.name} MISSING"
    chk = _get(f"{BASE}/{rel}.CHECKSUM")
    if chk is not None and chk.split()[0].decode() != hashlib.sha256(body).hexdigest():
        return f"{path.name} BAD_CHECKSUM"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return f"{path.name} OK {len(body)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    a = ap.parse_args()
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    jobs = [(k, a.symbol, d0 + timedelta(i)) for i in range((d1 - d0).days + 1) for k in SETS]
    with ThreadPoolExecutor(6) as ex:
        for line in ex.map(lambda j: fetch(*j), jobs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
