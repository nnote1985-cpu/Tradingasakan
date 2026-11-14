"""Build a data patch for a UTC day that data.binance.vision never published as klines.

    uv run python scripts/build_gap_patch.py --day 2026-10-28 --check-day 2026-10-27 \
        --id gap1 --out configs/data-patches/gap1.json

The BTCUSDT perpetual (futures/um) has no 1d or 1h kline for 2026-10-28 in the archive: the daily
files are missing and the monthly files have 30 days. The archive does publish that day's trades
file, so the missing bars are rebuilt from it: open = first trade, high/low = extremes, close = last
trade, volume, quote volume, trade count and taker-buy volumes are sums over the bar. The same
method is first run on --check-day, a day the archive does have, and the patch is only written if
it reproduces that day's archive 1d bar and all 24 archive 1h bars exactly (prices, volumes and
trade counts). Every file used is checked against its published .CHECKSUM.

Nothing here touches a book: the patch is a JSON file that scripts/paper_run.py --continuation and
scripts/risk_check.py --data-patch apply when they read the archive (docs/data-patches.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um"
SYMBOL = "BTCUSDT"
HEADER = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
          "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (fixed https archive host)
        return r.read()


def get_verified(url: str) -> tuple[bytes, str]:
    blob = _get(url)
    want = _get(url + ".CHECKSUM").decode().split()[0].lower()
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise SystemExit(f"checksum mismatch for {url}")
    return blob, got


def unzip(blob: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0])


def ms(d: date, hours: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000) + hours * 3_600_000


def bars_from_trades(raw: bytes, day: date) -> dict[str, list[list[str]]]:
    """1d bar and 24 1h bars, in the archive's kline CSV columns, from one day's trades CSV."""
    def empty():
        return {"o": None, "h": None, "l": None, "c": None, "v": Decimal(0), "q": Decimal(0), "n": 0,
                "tv": Decimal(0), "tq": Decimal(0)}

    day_bar, hours = empty(), [empty() for _ in range(24)]
    lo, hi = ms(day), ms(day + timedelta(days=1))
    rows = csv.reader(io.StringIO(raw.decode()))
    for row in rows:
        if not row or not row[0].isdigit():
            continue                                    # header
        _id, price, qty, quote, t, is_buyer_maker = row
        t = int(t)
        if not lo <= t < hi:
            raise SystemExit(f"trade {_id} at {t} is outside {day}")
        p, v, q = Decimal(price), Decimal(qty), Decimal(quote)
        for b in (day_bar, hours[(t - lo) // 3_600_000]):
            if b["o"] is None:
                b["o"] = b["h"] = b["l"] = p
            b["h"], b["l"], b["c"] = max(b["h"], p), min(b["l"], p), p
            b["v"] += v
            b["q"] += q
            b["n"] += 1
            if is_buyer_maker == "false":               # the buyer was the taker
                b["tv"] += v
                b["tq"] += q

    def row_of(b, start: int, length: int) -> list[str]:
        if b["o"] is None:
            raise SystemExit(f"no trades in the bar starting {start}")
        return [str(start), str(b["o"]), str(b["h"]), str(b["l"]), str(b["c"]), str(b["v"]), str(start + length - 1),
                str(b["q"]), str(b["n"]), str(b["tv"]), str(b["tq"]), "0"]

    return {"1d": [row_of(day_bar, lo, 86_400_000)],
            "1h": [row_of(h, lo + i * 3_600_000, 3_600_000) for i, h in enumerate(hours)]}


def archive_rows(interval: str, day: date) -> list[list[str]]:
    blob, _ = get_verified(f"{BASE}/daily/klines/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{day.isoformat()}.zip")
    return [r for r in csv.reader(io.StringIO(unzip(blob).decode())) if r and r[0].isdigit()]


def same(a: list[str], b: list[str]) -> bool:
    """Numerically equal in every column except the always-zero 'ignore'."""
    return all(Decimal(x) == Decimal(y) for x, y in zip(a[:11], b[:11], strict=True))


def trades(day: date) -> tuple[bytes, dict]:
    url = f"{BASE}/daily/trades/{SYMBOL}/{SYMBOL}-trades-{day.isoformat()}.zip"
    blob, sha = get_verified(url)
    raw = unzip(blob)
    return raw, {"url": url, "zip_sha256": sha, "csv_sha256": hashlib.sha256(raw).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, type=date.fromisoformat, help="the UTC day the archive lacks")
    ap.add_argument("--check-day", required=True, type=date.fromisoformat,
                    help="a day the archive has, used to prove the method reproduces it exactly")
    ap.add_argument("--id", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    raw, check_src = trades(args.check_day)
    rebuilt = bars_from_trades(raw, args.check_day)
    for interval in ("1d", "1h"):
        want = archive_rows(interval, args.check_day)
        got = rebuilt[interval]
        if len(want) != len(got) or not all(same(a, b) for a, b in zip(got, want, strict=True)):
            raise SystemExit(f"{interval} bars rebuilt from trades do not match the archive on {args.check_day}")

    raw, src = trades(args.day)
    bars = bars_from_trades(raw, args.day)
    patch = {
        "id": args.id,
        "market": "futures/um", "symbol": SYMBOL, "kind": "klines",
        "days": [args.day.isoformat()],
        "reason": f"data.binance.vision has no {SYMBOL} perpetual 1d or 1h kline for {args.day} "
                  "(daily files 404, monthly files skip the day)",
        "method": "bars rebuilt from the archive's checksum-verified trades file for the day; "
                  "open/high/low/close from trade prices, volumes and taker-buy volumes summed, count = trades",
        "source": src,
        "check": {"day": args.check_day.isoformat(), "source": check_src,
                  "result": "rebuilt 1d bar and 24 1h bars equal the archive's in every column"},
        "rule": "a patch row is used only when the archive has no row for that bar; an archive row always wins",
        "columns": HEADER,
        "rows": bars,
        "built_at": datetime.now(UTC).isoformat(),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(patch, indent=1) + "\n")
    print(json.dumps({"out": args.out, "1d": bars["1d"][0][:5], "check": "ok"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
