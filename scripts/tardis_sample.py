"""Download Tardis.dev free sample days and shrink them on the fly (run on the VM; stdlib only, py3.10).

    python3 scripts/tardis_sample.py --out /var/lib/tardis-sample [--days 2025-11-01,2025-12-01,2026-11-01]

Tardis gives the first day of each month free without an API key. For each day and market
(binance = spot, binance-futures = perp) it streams, without saving the raw files:

- book_snapshot_25 -> <out>/<market>/BTCUSDT/book_<day>.jsonl.gz: the book at the end of every
  second, top 20 levels, in the same line format as scripts/orderbook_collect.py
  ({"t", "lt", "mid", "spr_bps", "imb5", "imb20", "b", "a"}), so the same feature code reads both.
- trades -> <out>/<market>/BTCUSDT/trades_<day>.csv.gz: per-second taker buy/sell volume,
  trade count, VWAP, high, low.

Seconds with no book update are not written (the book did not change): forward-fill when loading.
Default days avoid the C2 window (2026-01..10). Raw files are large (book ~1-3 GB per day);
the outputs are roughly 10-20 MB per day per market.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://datasets.tardis.dev/v1"
MARKETS = {"spot": "binance", "perp": "binance-futures"}
LEVELS = 20


def _stream(url: str) -> io.TextIOWrapper:
    r = urllib.request.urlopen(url, timeout=120)  # noqa: S310 (fixed https host)
    return io.TextIOWrapper(gzip.GzipFile(fileobj=r), encoding="utf-8")


def _imb(side_b: list[list[float]], side_a: list[list[float]], n: int) -> float | None:
    bq = sum(q for _, q in side_b[:n])
    aq = sum(q for _, q in side_a[:n])
    return round((bq - aq) / (bq + aq), 6) if bq + aq > 0 else None


def book_record(line: str, cols: dict[str, int], sec: int) -> dict | None:
    f = line.rstrip("\n").split(",")
    bids, asks = [], []
    for i in range(LEVELS):
        for side, out in (("bids", bids), ("asks", asks)):
            pi = cols.get(f"{side}[{i}].price")
            if pi is None or not f[pi]:
                continue
            out.append([float(f[pi]), float(f[cols[f"{side}[{i}].amount"]])])
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    return {
        "t": (sec + 1) * 1000,  # book state at the end of this second
        "lt": int(f[cols["local_timestamp"]]) // 1000,
        "mid": round(mid, 4),
        "spr_bps": round((asks[0][0] - bids[0][0]) / mid * 1e4, 4),
        "imb5": _imb(bids, asks, 5),
        "imb20": _imb(bids, asks, LEVELS),
        "b": bids,
        "a": asks,
    }


def reduce_book(url: str, dest: Path) -> int:
    src = _stream(url)
    header = src.readline().rstrip("\n").split(",")
    cols = {c: i for i, c in enumerate(header)}
    ts_i = cols["timestamp"]
    n, last, last_sec = 0, None, None
    with gzip.open(dest, "wt") as out:
        for line in src:
            sec = int(line.split(",", ts_i + 1)[ts_i]) // 1_000_000
            if last_sec is not None and sec != last_sec:
                rec = book_record(last, cols, last_sec)
                if rec:
                    out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                    n += 1
            last, last_sec = line, sec
        if last is not None:
            rec = book_record(last, cols, last_sec)
            if rec:
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                n += 1
    return n


def reduce_trades(url: str, dest: Path) -> int:
    src = _stream(url)
    cols = {c: i for i, c in enumerate(src.readline().rstrip("\n").split(","))}
    ti, si, pi, ai = cols["timestamp"], cols["side"], cols["price"], cols["amount"]
    n = 0
    with gzip.open(dest, "wt") as out:
        out.write("t,buy,sell,n,vwap,high,low\n")
        cur, buy, sell, cnt, pv, hi, lo = None, 0.0, 0.0, 0, 0.0, 0.0, 0.0

        def flush() -> None:
            nonlocal n
            if cur is not None and cnt:
                out.write(f"{(cur + 1) * 1000},{buy:.6f},{sell:.6f},{cnt},{pv / (buy + sell):.4f},{hi},{lo}\n")
                n += 1

        for line in src:
            f = line.rstrip("\n").split(",")
            sec = int(f[ti]) // 1_000_000
            if sec != cur:
                flush()
                cur, buy, sell, cnt, pv, hi, lo = sec, 0.0, 0.0, 0, 0.0, 0.0, float("inf")
            p, q = float(f[pi]), float(f[ai])
            if f[si] == "buy":
                buy += q
            else:
                sell += q
            cnt += 1
            pv += p * q
            hi, lo = max(hi, p), min(lo, p)
        flush()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", default="2025-11-01,2025-12-01,2026-11-01")
    ap.add_argument("--symbol", default="BTCUSDT")
    a = ap.parse_args()
    for day in a.days.split(","):
        y, m, d = day.split("-")
        for market, exch in MARKETS.items():
            folder = Path(a.out) / market / a.symbol
            folder.mkdir(parents=True, exist_ok=True)
            for kind, fn in (("book_snapshot_25", reduce_book), ("trades", reduce_trades)):
                name = "book" if kind.startswith("book") else "trades"
                ext = "jsonl.gz" if name == "book" else "csv.gz"
                dest = folder / f"{name}_{day}.{ext}"
                if dest.exists():
                    print(f"{dest} exists, skip", flush=True)
                    continue
                tmp = dest.with_name(dest.name + ".part")
                url = f"{BASE}/{exch}/{kind}/{y}/{m}/{d}/{a.symbol}.csv.gz"
                try:
                    rows = fn(url, tmp)
                except Exception as e:  # noqa: BLE001 (report and continue with the next file)
                    print(f"FAIL {url}: {e}", file=sys.stderr, flush=True)
                    continue
                tmp.rename(dest)
                print(f"OK {dest} rows={rows} size={dest.stat().st_size}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
