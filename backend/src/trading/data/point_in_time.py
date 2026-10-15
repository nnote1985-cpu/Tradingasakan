"""Point-in-time views of the raw store (BUILDPLAN v4.2 §5.1, §5.2).

A bar covers ``[open_at, close_at)`` and becomes usable at
``available_at = max(close_at, received_at)``, where ``received_at`` is when the
file holding it arrived. A view ``as_of`` t sees, for each file, the latest
revision received by t, and only the bars available by t. Missing bars stay
missing: gaps are reported, never forward-filled.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pandas as pd

from trading.data.raw_store import RawRecord, RawStore
from trading.market_data.binance_public import parse_funding_csv, parse_klines_csv

INTERVALS = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}


def _unzip(blob: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0])


def visible_files(store: RawStore, dataset: str, symbol: str, as_of: datetime) -> list[RawRecord]:
    """For each file of ``dataset``/``symbol``, the latest revision received by ``as_of``."""
    best: dict[str, RawRecord] = {}
    for r in store.records(received_by=as_of):
        if r.meta.get("dataset") == dataset and r.meta.get("symbol") == symbol:
            if r.source_key not in best or r.revision > best[r.source_key].revision:
                best[r.source_key] = r
    return [best[k] for k in sorted(best)]


def bars(store: RawStore, dataset: str, symbol: str, as_of: datetime) -> tuple[pd.DataFrame, list[RawRecord]]:
    """1h bars available by ``as_of`` with ``close_at`` and ``available_at`` columns,
    and the file revisions they came from."""
    files = visible_files(store, dataset, symbol, as_of)
    frames = []
    for r in files:
        df = parse_klines_csv(_unzip(store.read(r)))
        df["close_at"] = df.index + INTERVALS["1h"]
        df["available_at"] = df["close_at"].clip(lower=pd.Timestamp(r.received_at))
        frames.append(df)
    if not frames:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_at", "available_at"])
        empty.index = pd.DatetimeIndex([], tz="UTC", name="open_time")
        return empty, files
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out[out["available_at"] <= pd.Timestamp(as_of)], files


def funding_prints(store: RawStore, symbol: str, as_of: datetime) -> tuple[pd.Series, list[RawRecord]]:
    """Actual funding prints available by ``as_of`` (a print is usable from its own time)."""
    files = visible_files(store, "funding", symbol, as_of)
    parts = []
    for r in files:
        s = parse_funding_csv(_unzip(store.read(r)))
        s.index = s.index.round("1h")
        avail = pd.Series(s.index, index=s.index).clip(lower=pd.Timestamp(r.received_at))
        parts.append(s[avail <= pd.Timestamp(as_of)])
    if not parts:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"), name="funding_rate"), files
    f = pd.concat(parts).sort_index()
    return f[~f.index.duplicated(keep="last")], files


def gaps(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, step: pd.Timedelta = INTERVALS["1h"]) -> list[str]:
    """Bar open times in ``[start, end)`` with no bar."""
    want = pd.date_range(start, end, freq=step, inclusive="left")
    return [t.isoformat() for t in want.difference(df.index)]


def resample_4h(h: pd.DataFrame) -> pd.DataFrame:
    """4h bars (00/04/08/12/16/20 UTC) built only from four present 1h bars."""
    if h.empty:
        return h.copy()
    g = h.groupby(h.index.floor("4h"))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].size(),
                        "available_at": g["available_at"].max()})
    out = out[out["n"] == 4].drop(columns="n")
    out["close_at"] = out.index + INTERVALS["4h"]
    return out
