"""Historical data from Binance public data (data.binance.vision), USDⓈ-M futures.

Data rights and coverage are a DISCOVERY item (BUILDPLAN v4.2 §4.1); this module
only downloads, checksum-verifies and normalises what the source publishes.
Gaps are kept as gaps.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import ssl
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def _get(url: str, timeout: int = 60) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=_ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _to_utc(series: pd.Series) -> pd.Series:
    """Binance switched some datasets from ms to µs; detect by magnitude."""
    v = pd.to_numeric(series)
    unit = "us" if v.iloc[0] > 10**14 else "ms"
    return pd.to_datetime(v, unit=unit, utc=True)


def parse_klines_csv(raw: bytes) -> pd.DataFrame:
    first = raw.split(b"\n", 1)[0].split(b",")[0].strip()
    header = 0 if not first.isdigit() else None
    df = pd.read_csv(io.BytesIO(raw), header=header)
    df = df.iloc[:, :12]
    df.columns = KLINE_COLUMNS
    df["open_time"] = _to_utc(df["open_time"])
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]].astype(float)
    return df


def parse_funding_csv(raw: bytes) -> pd.Series:
    """Columns: calc_time, funding_interval_hours, last_funding_rate."""
    first = raw.split(b"\n", 1)[0].split(b",")[0].strip()
    header = 0 if not first.isdigit() else None
    df = pd.read_csv(io.BytesIO(raw), header=header)
    t = _to_utc(df.iloc[:, 0])
    rate = pd.to_numeric(df.iloc[:, -1])
    return pd.Series(rate.to_numpy(), index=pd.DatetimeIndex(t), name="funding_rate")


def _download_zip(url: str, raw_dir: Path) -> tuple[bytes | None, dict]:
    name = url.rsplit("/", 1)[1]
    path = raw_dir / name
    if path.exists():
        blob = path.read_bytes()
    else:
        blob = _get(url)
        if blob is None:
            return None, {"file": name, "status": "MISSING"}
        path.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    checksum = _get(url + ".CHECKSUM")
    expected = checksum.decode().split()[0] if checksum else None
    status = "VERIFIED" if expected == sha else ("NO_CHECKSUM" if expected is None else "MISMATCH")
    if status == "MISMATCH":
        path.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {name}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        inner = z.read(z.namelist()[0])
    return inner, {"file": name, "sha256": sha, "status": status}


def download(symbol: str, start: date, end: date, out_dir: Path,
             intervals=("1h", "1m"), funding: bool = True) -> dict:
    """Download monthly archives, write ``{symbol}_{interval}.parquet`` and
    ``{symbol}_funding.parquet`` plus a manifest of every file and checksum."""
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"source": BASE, "symbol": symbol, "requested": [str(start), str(end)], "files": []}

    for interval in intervals:
        frames = []
        for y, m in _months(start, end):
            url = f"{BASE}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{y}-{m:02d}.zip"
            inner, info = _download_zip(url, raw_dir)
            manifest["files"].append(info)
            if inner is not None:
                frames.append(parse_klines_csv(inner))
        if frames:
            df = pd.concat(frames).sort_index()
            df = df[~df.index.duplicated(keep="first")]
            df.to_parquet(out_dir / f"{symbol}_{interval}.parquet")
            manifest[f"coverage_{interval}"] = [str(df.index[0]), str(df.index[-1]), int(len(df))]

    if funding:
        parts = []
        for y, m in _months(start, end):
            url = f"{BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip"
            inner, info = _download_zip(url, raw_dir)
            manifest["files"].append(info)
            if inner is not None:
                parts.append(parse_funding_csv(inner))
        if parts:
            f = pd.concat(parts).sort_index()
            f = f[~f.index.duplicated(keep="first")]
            f.to_frame().to_parquet(out_dir / f"{symbol}_funding.parquet")
            manifest["coverage_funding"] = [str(f.index[0]), str(f.index[-1]), int(len(f))]

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def load(out_dir: Path, symbol: str, interval: str) -> pd.DataFrame:
    return pd.read_parquet(Path(out_dir) / f"{symbol}_{interval}.parquet")


def load_funding(out_dir: Path, symbol: str) -> pd.Series | None:
    p = Path(out_dir) / f"{symbol}_funding.parquet"
    return pd.read_parquet(p)["funding_rate"] if p.exists() else None
