"""trend_breakout_v1.0 — BUILDPLAN v4.2 §6.

Pure strategy: reads closed, validated bars only and emits a shared candidate
stream. It never reads portfolio state, AI output or account data.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from trading.features.indicators import atr_wilder, ema, previous_high, previous_low

STRATEGY_ID = "trend_breakout_v1.0"

H1 = pd.Timedelta(hours=1)
H4 = pd.Timedelta(hours=4)


@dataclass(frozen=True)
class StrategyParams:
    ema_fast_4h: int = 50
    ema_slow_4h: int = 200
    ema_local_1h: int = 20
    atr_n: int = 14
    breakout_lookback: int = 20
    warmup_4h_bars: int = 600
    warmup_1h_bars: int = 250


REQUIRED_FEATURES = (
    "close4h", "ema50_4h", "ema200_4h", "close1h",
    "ema20_1h", "previous20_high", "previous20_low", "atr14_1h",
)


def candidate_signal(features: dict) -> str:
    """Signal logic exactly as §6.5 (LONG / SHORT / NO_TRADE)."""
    if features.get("quality") != "VALID" or not features.get("warmup_ok"):
        return "NO_TRADE"
    if any(k not in features for k in REQUIRED_FEATURES):
        return "NO_TRADE"
    f = {k: Decimal(str(features[k])) for k in REQUIRED_FEATURES}
    if any(not v.is_finite() for v in f.values()):
        return "NO_TRADE"
    if f["atr14_1h"] <= 0 or f["previous20_high"] < f["previous20_low"]:
        return "NO_TRADE"
    long_ok = (
        f["ema50_4h"] > f["ema200_4h"]
        and f["close4h"] > f["ema200_4h"]
        and f["close1h"] > f["previous20_high"]
        and f["close1h"] > f["ema20_1h"]
    )
    short_ok = (
        f["ema50_4h"] < f["ema200_4h"]
        and f["close4h"] < f["ema200_4h"]
        and f["close1h"] < f["previous20_low"]
        and f["close1h"] < f["ema20_1h"]
    )
    if long_ok and short_ok:
        return "NO_TRADE"
    return "LONG" if long_ok else "SHORT" if short_ok else "NO_TRADE"


def _contiguous_count(valid: np.ndarray) -> np.ndarray:
    """Number of consecutive valid bars ending at each index (0 where invalid)."""
    out = np.zeros(len(valid), dtype=np.int64)
    run = 0
    for i, ok in enumerate(valid):
        run = run + 1 if ok else 0
        out[i] = run
    return out


def regularize_h1(h1: pd.DataFrame) -> pd.DataFrame:
    """Reindex 1h bars onto a full hourly grid. Missing hours become NaN rows with
    ``valid=False`` — gaps are never forward-filled (§5.2)."""
    h1 = h1.sort_index()
    h1 = h1[~h1.index.duplicated(keep="first")]
    grid = pd.date_range(h1.index[0], h1.index[-1], freq="1h", tz="UTC")
    out = h1.reindex(grid)
    out.index.name = "open_time"
    ok = out[["open", "high", "low", "close"]].notna().all(axis=1)
    if "valid" in out.columns:
        ok &= out["valid"].fillna(False).astype(bool)
    out["valid"] = ok
    return out


def build_h4(h1: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1h → 4h on 00/04/08/12/16/20 UTC boundaries. A 4h bar is valid
    only if all four 1h bars are valid."""
    g = h1.groupby(h1.index.floor("4h"))
    h4 = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "n_valid": g["valid"].sum(),
    })
    full = pd.date_range(h4.index[0], h4.index[-1], freq="4h", tz="UTC")
    h4 = h4.reindex(full)
    h4["valid"] = h4["n_valid"].fillna(0).astype(int) == 4
    h4.loc[~h4["valid"], ["open", "high", "low", "close"]] = np.nan
    h4.index.name = "open_time"
    h4["close_time"] = h4.index + H4
    return h4.drop(columns="n_valid")


def build_features(h1_raw: pd.DataFrame, params: StrategyParams = StrategyParams()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (h1 features, h4 features).

    h1 row t describes the 1h bar [t, t+1h); its signal is available at
    ``close_time = t + 1h`` and uses only the latest 4h bar whose close_time is
    <= that time (point-in-time, no look-ahead).
    """
    p = params
    h1 = regularize_h1(h1_raw)
    h4 = build_h4(h1)

    h4["ema_fast"] = ema(h4["close"].to_numpy(), p.ema_fast_4h)
    h4["ema_slow"] = ema(h4["close"].to_numpy(), p.ema_slow_4h)
    h4["run"] = _contiguous_count(h4["valid"].to_numpy())

    h1["close_time"] = h1.index + H1
    h1["ema_local"] = ema(h1["close"].to_numpy(), p.ema_local_1h)
    h1["atr"] = atr_wilder(h1["high"].to_numpy(), h1["low"].to_numpy(), h1["close"].to_numpy(), p.atr_n)
    h1["prev_high"] = previous_high(h1["high"].to_numpy(), p.breakout_lookback)
    h1["prev_low"] = previous_low(h1["low"].to_numpy(), p.breakout_lookback)
    h1["run"] = _contiguous_count(h1["valid"].to_numpy())

    # Latest *closed* 4h bar at each 1h close time.
    h4_by_close = h4.set_index("close_time")[["close", "ema_fast", "ema_slow", "run", "valid"]]
    h4_by_close.columns = ["close4h", "ema50_4h", "ema200_4h", "run4h", "valid4h"]
    aligned = h4_by_close.reindex(h1["close_time"], method="ffill")
    aligned.index = h1.index
    h1 = h1.join(aligned)

    h1["warmup_ok"] = (
        (h1["run"] >= p.warmup_1h_bars)
        & (h1["run4h"].fillna(0) >= p.warmup_4h_bars)
        & h1["valid4h"].fillna(False).astype(bool)
    )
    h1["signal"] = _vector_signal(h1)
    return h1, h4


def _vector_signal(h1: pd.DataFrame) -> np.ndarray:
    """Vectorised equivalent of :func:`candidate_signal` for backtests.

    ``tests/unit/test_strategy.py`` asserts it agrees with the scalar version.
    """
    c = h1["close"].to_numpy()
    e50 = h1["ema50_4h"].to_numpy()
    e200 = h1["ema200_4h"].to_numpy()
    c4 = h1["close4h"].to_numpy()
    e20 = h1["ema_local"].to_numpy()
    ph = h1["prev_high"].to_numpy()
    pl = h1["prev_low"].to_numpy()
    atr = h1["atr"].to_numpy()
    finite = np.isfinite(np.vstack([c, e50, e200, c4, e20, ph, pl, atr])).all(axis=0)
    ok = h1["valid"].to_numpy() & h1["warmup_ok"].to_numpy() & finite
    with np.errstate(invalid="ignore"):
        ok &= (atr > 0) & (ph >= pl)
        long_ok = ok & (e50 > e200) & (c4 > e200) & (c > ph) & (c > e20)
        short_ok = ok & (e50 < e200) & (c4 < e200) & (c < pl) & (c < e20)
    sig = np.full(len(h1), "NO_TRADE", dtype=object)
    sig[long_ok & ~short_ok] = "LONG"
    sig[short_ok & ~long_ok] = "SHORT"
    return sig


def features_row(h1: pd.DataFrame, ts: pd.Timestamp) -> dict:
    """Scalar feature dict for one 1h bar, in the §6.5 contract shape."""
    r = h1.loc[ts]
    return {
        "quality": "VALID" if bool(r["valid"]) else "INVALID",
        "warmup_ok": bool(r["warmup_ok"]),
        "close4h": r["close4h"],
        "ema50_4h": r["ema50_4h"],
        "ema200_4h": r["ema200_4h"],
        "close1h": r["close"],
        "ema20_1h": r["ema_local"],
        "previous20_high": r["prev_high"],
        "previous20_low": r["prev_low"],
        "atr14_1h": r["atr"],
    }
