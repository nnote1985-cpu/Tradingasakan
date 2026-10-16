"""Download BTCUSDT USD-M daily `bookDepth` and `metrics` files from data.binance.vision (R8).

    uv run python research/spike/download_depth.py [--start 2023-01-01] [--end 2025-12-31]

Each zip is checked against its .CHECKSUM (SHA256). Writes data/binance_um_depth/<set>/ and a
manifest.json with one row per file: OK, MISSING (404) or BAD_CHECKSUM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um/daily"
SETS = ("bookDepth", "metrics")
OUT = Path("data/binance_um_depth")


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return bytes(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch(kind: str, day: date) -> dict:
    name = f"BTCUSDT-{kind}-{day.isoformat()}.zip"
    path = OUT / kind / name
    url = f"{BASE}/{kind}/BTCUSDT/{name}"
    if path.exists():
        body: bytes | None = path.read_bytes()
    else:
        body = _get(url)
        if body is None:
            return {"file": name, "status": "MISSING"}
    assert body is not None
    check = _get(url + ".CHECKSUM")
    want = check.decode().split()[0] if check else None
    got = hashlib.sha256(body).hexdigest()
    if want != got:
        return {"file": name, "status": "BAD_CHECKSUM", "sha256": got, "expected": want}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {"file": name, "status": "OK", "sha256": got}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-12-31")
    a = ap.parse_args()
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    jobs = [(k, d0 + timedelta(days=i)) for k in SETS for i in range((d1 - d0).days + 1)]
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda j: fetch(*j), jobs))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps({"start": a.start, "end": a.end, "files": rows}, indent=1))
    for k in SETS:
        st = [r["status"] for r in rows if f"-{k}-" in r["file"]]
        print(k, {s: st.count(s) for s in sorted(set(st))})


if __name__ == "__main__":
    main()
