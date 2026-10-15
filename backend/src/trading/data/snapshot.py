"""Deterministic market snapshots (BUILDPLAN v4.2 §5.2, §20.1 market_snapshots).

A snapshot is everything a decision at ``as_of`` may use, built only from the
raw store as it stood at ``as_of``: the last closed bars, features computed from
closed bars, data-quality flags, pointers to the exact raw file revisions, and a
sha256 of its canonical JSON. ``replay`` rebuilds it from the raw store and
must give the same hash; later revisions or later files never change it.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trading.alerts.live_data import FUNDING_POLICY, estimate_funding
from trading.data.point_in_time import bars, funding_prints, gaps, resample_4h
from trading.data.raw_store import RawStore
from trading.features.indicators import atr_wilder, ema

SCHEMA = "market_snapshot.v1"
FEATURE_VERSION = "pit_features.v1"
LOOKBACK_DAYS = 30
STALE_AFTER = pd.Timedelta(hours=36)    # archive days land about 8h after they close


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _num(x) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _bar(df: pd.DataFrame) -> dict | None:
    if df.empty:
        return None
    r = df.iloc[-1]
    return {"open_at": df.index[-1].isoformat(), "close_at": pd.Timestamp(r["close_at"]).isoformat(),
            "available_at": pd.Timestamp(r["available_at"]).isoformat(),
            **{k: _num(r[k]) for k in ("open", "high", "low", "close", "volume")}}


def _funding(store: RawStore, symbol: str, as_of: pd.Timestamp, premium: pd.DataFrame) -> tuple[dict, list[str]]:
    """funding_policy.v1 signal: mean of the last 7 complete UTC days' summed prints,
    actual prints first, premium-index proxy only after the last actual print."""
    actual, _ = funding_prints(store, symbol, as_of.to_pydatetime())
    last_actual = actual.index.max() if len(actual) else None
    proxy = pd.Series(dtype=float)
    if not premium.empty:
        p = premium["close"]
        full = p.groupby(p.index.floor("8h")).size()
        est = estimate_funding(p)
        est = est[(full == 8).reindex(est.index - pd.Timedelta(hours=8)).fillna(False).to_numpy()]
        proxy = est[(est.index <= as_of) & ((est.index > last_actual) if last_actual is not None else True)]
    prints = pd.concat([actual, proxy]).sort_index()
    today = as_of.floor("1D")
    days = [today - pd.Timedelta(days=i) for i in range(7, 0, -1)]
    window, problems = [], []
    for d in days:
        stamps = [d + pd.Timedelta(hours=h) for h in (0, 8, 16)]
        have = [t for t in stamps if t in prints.index]
        src = "proxy" if any(t in proxy.index for t in have) else "actual"
        if len(have) < 3:
            window.append({"day": str(d.date()), "funding": None, "source": "missing"})
            problems.append(f"funding incomplete on {d.date()}")
        else:
            window.append({"day": str(d.date()), "funding": _num(prints.loc[have].sum()), "source": src})
    vals = [w["funding"] for w in window]
    mean = None if any(v is None for v in vals) else float(np.mean(vals))
    proxy_days = sum(w["source"] == "proxy" for w in window)
    return {"policy": FUNDING_POLICY["version"], "last_actual_print": last_actual.isoformat() if last_actual is not None else None,
            "window": window, "mean_7d_per_day": mean, "proxy_days": proxy_days,
            "hot": None if mean is None else bool(mean > FUNDING_POLICY["threshold_per_day"])}, problems


def build_snapshot(store: RawStore, symbol: str, as_of: datetime) -> dict:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    t = pd.Timestamp(as_of).tz_convert("UTC")
    start = t.floor("1D") - pd.Timedelta(days=LOOKBACK_DAYS)
    pointers, problems, data = [], [], {}
    for name in ("perp_klines_1h", "spot_klines_1h", "premium_index_1h"):
        df, files = bars(store, name, symbol, t.to_pydatetime())
        df = df[df.index >= start]
        used = set(df.index.floor("1D").strftime("%Y-%m-%d"))
        pointers += [f.pointer for f in files if f.meta.get("day") in used]
        data[name] = df
    _, ffiles = funding_prints(store, symbol, t.to_pydatetime())
    pointers += [f.pointer for f in ffiles]

    perp, spot = data["perp_klines_1h"], data["spot_klines_1h"]
    last_close = perp.index.max() + pd.Timedelta(hours=1) if len(perp) else start
    quality: dict = {"gaps": {}, "stale": {}, "missing": [], "short_history": {}}
    for name, df in data.items():
        if len(df) and df.index.min() > start:
            # The store starts after the lookback window: features would rest on less history.
            quality["short_history"][name] = df.index.min().isoformat()
            problems.append(f"{name}: history starts {df.index.min()} after lookback start {start}")
        g = gaps(df, df.index.min() if len(df) else start, last_close)
        if g:
            quality["gaps"][name] = g
            problems.append(f"{name}: {len(g)} missing 1h bars")
        if df.empty:
            quality["missing"].append(name)
            problems.append(f"{name}: no data")
        else:
            age = t - pd.Timestamp(df["close_at"].iloc[-1])
            quality["stale"][name] = {"age_seconds": int(age.total_seconds()), "stale": bool(age > STALE_AFTER)}
            if age > STALE_AFTER:
                problems.append(f"{name}: last bar closed {age} before as_of")

    h4 = resample_4h(perp)
    features: dict = {"version": FEATURE_VERSION}
    if len(h4) >= 50:
        c = h4["close"].to_numpy()
        features["ema20_4h"] = _num(ema(c, 20)[-1])
        features["ema50_4h"] = _num(ema(c, 50)[-1])
    if len(perp) >= 15:
        features["atr14_1h"] = _num(atr_wilder(perp["high"].to_numpy(), perp["low"].to_numpy(), perp["close"].to_numpy())[-1])
    if len(perp) >= 25 and not quality["gaps"].get("perp_klines_1h"):
        features["return_24h"] = _num(perp["close"].iloc[-1] / perp["close"].iloc[-25] - 1)
    if len(perp) and len(spot) and perp.index[-1] == spot.index[-1]:
        features["basis"] = _num(perp["close"].iloc[-1] / spot["close"].iloc[-1] - 1)
    funding, fprob = _funding(store, symbol, t, data["premium_index_1h"])
    problems += fprob

    body = {"schema": SCHEMA, "symbol": symbol, "as_of": t.isoformat(), "feature_version": FEATURE_VERSION,
            "params": {"lookback_days": LOOKBACK_DAYS, "stale_after_seconds": int(STALE_AFTER.total_seconds())},
            "last_bar": {"perp_1h": _bar(perp), "spot_1h": _bar(spot), "perp_4h": _bar(h4)},
            "features": features, "funding": funding,
            "quality": {**quality, "complete": not problems, "problems": problems},
            "raw_pointers": sorted(pointers, key=lambda p: (p["source_key"], p["revision"]))}
    return {**body, "sha256": hashlib.sha256(canonical(body)).hexdigest()}


def replay(store: RawStore, snap: dict) -> dict:
    """Rebuild ``snap`` from the raw store; ``ok`` is True when the hash matches."""
    again = build_snapshot(store, snap["symbol"], datetime.fromisoformat(snap["as_of"]))
    return {"ok": again["sha256"] == snap["sha256"], "recorded": snap["sha256"], "replayed": again["sha256"]}


class SnapshotLog:
    """Snapshots as taken, one canonical JSON line each, never rewritten."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, snap: dict, taken_at: datetime | None = None) -> None:
        line = {"taken_at": (taken_at or datetime.now(UTC)).isoformat(), "snapshot": snap}
        with self.path.open("a") as f:
            f.write(canonical(line).decode() + "\n")

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x)["snapshot"] for x in self.path.read_text().splitlines() if x.strip()]

    def replay_all(self, store: RawStore) -> list[dict]:
        return [{"as_of": s["as_of"], **replay(store, s)} for s in self.all()]
