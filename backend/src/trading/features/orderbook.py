"""Order book and order flow features: imbalance (OBI), trade delta / CVD, liquidity walls.

Pure functions on numpy/pandas so the same code runs on
- the free data.binance.vision sets (aggTrades, bookDepth) used by the research prototype, and
- the VM collector's L2 snapshots (scripts/orderbook_collect.py: {"t", "mid", "b": [[p, q]], "a": [[p, q]]}).

Sign convention everywhere: positive = buy side stronger (more bid depth / more taker buying).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

Level = Sequence[float]  # [price, qty]


# ---------------------------------------------------------------- order book imbalance


def imbalance(bid: float | np.ndarray, ask: float | np.ndarray) -> float | np.ndarray:
    """(bid - ask) / (bid + ask) in [-1, 1]; NaN when both sides are empty."""
    b = np.asarray(bid, dtype=float)
    a = np.asarray(ask, dtype=float)
    tot = b + a
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(tot > 0, (b - a) / tot, np.nan)
    return float(out) if out.ndim == 0 else out


def obi_levels(bids: Sequence[Level], asks: Sequence[Level], n: int) -> float:
    """Imbalance of the summed quantity in the top ``n`` levels of each side."""
    return float(imbalance(sum(q for _, q in bids[:n]), sum(q for _, q in asks[:n])))


def obi_band(bids: Sequence[Level], asks: Sequence[Level], mid: float, pct: float) -> float:
    """Imbalance of notional within ``pct`` percent of mid (comparable with bookDepth bands)."""
    lo, hi = mid * (1 - pct / 100), mid * (1 + pct / 100)
    b = sum(p * q for p, q in bids if p >= lo)
    a = sum(p * q for p, q in asks if p <= hi)
    return float(imbalance(b, a))


def obi_from_bookdepth(df: pd.DataFrame, pct: float = 1.0) -> pd.Series:
    """Imbalance per snapshot from a data.binance.vision ``bookDepth`` frame.

    bookDepth rows: timestamp, percentage (-5..-1 and -0.2 bids, 0.2 and 1..5 asks), depth, notional, where depth is
    cumulative from the mid out to that percentage. Returns a Series indexed by snapshot time.
    """
    p = df.pivot_table(index="timestamp", columns="percentage", values="notional", aggfunc="last")
    if -pct not in p.columns or pct not in p.columns:
        raise ValueError(f"bookDepth has no +-{pct}% band")
    return pd.Series(imbalance(p[-pct].to_numpy(), p[pct].to_numpy()), index=p.index, name=f"obi{pct}")


# ---------------------------------------------------------------- order flow


def trade_flow_bars(trades: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Taker buy / sell volume per bar from a trades frame.

    ``trades`` needs a DatetimeIndex (UTC), ``qty`` and ``is_buyer_maker`` (True = taker sold).
    Returns buy, sell, delta (= buy - sell), delta_ratio (= delta / total, in [-1, 1]) and
    cvd (running sum of delta from the first bar).
    """
    sell_mask = trades["is_buyer_maker"].to_numpy(dtype=bool)
    qty = trades["qty"].to_numpy(dtype=float)
    side = pd.DataFrame({"buy": np.where(sell_mask, 0.0, qty), "sell": np.where(sell_mask, qty, 0.0)}, index=trades.index)
    out = side.resample(rule, label="left", closed="left").sum()
    out["delta"] = out["buy"] - out["sell"]
    out["delta_ratio"] = imbalance(out["buy"].to_numpy(), out["sell"].to_numpy())
    out["cvd"] = out["delta"].cumsum()
    return out


def cvd_slope(cvd: pd.Series, n: int) -> pd.Series:
    """Change in CVD over the last ``n`` bars (order flow trend)."""
    return cvd.diff(n)


# ---------------------------------------------------------------- liquidity walls


@dataclass(frozen=True)
class Wall:
    side: str  # "bid" or "ask"
    price: float
    qty: float
    dist_bps: float  # distance from mid in basis points (always >= 0)
    ratio: float  # qty / median qty of that side's levels


def find_walls(bids: Sequence[Level], asks: Sequence[Level], mid: float, *, k: float = 5.0, max_bps: float = 50.0) -> list[Wall]:
    """Levels at least ``k`` times the side's median size and within ``max_bps`` of mid, nearest first."""
    walls: list[Wall] = []
    for side, levels in (("bid", bids), ("ask", asks)):
        if not levels:
            continue
        med = float(np.median([q for _, q in levels]))
        if med <= 0:
            continue
        for p, q in levels:
            dist = abs(p - mid) / mid * 1e4
            if q >= k * med and dist <= max_bps:
                walls.append(Wall(side, float(p), float(q), dist, float(q) / med))
    return sorted(walls, key=lambda w: w.dist_bps)


def nearest_wall_bps(walls: Iterable[Wall], side: str) -> float:
    """Distance to the nearest wall on ``side``; inf when there is none."""
    return min((w.dist_bps for w in walls if w.side == side), default=float("inf"))


def near_depth_ratio_from_bookdepth(df: pd.DataFrame) -> pd.DataFrame:
    """Coarse liquidity-wall proxy from bookDepth when level detail is missing.

    For each side: notional per 1% of price within 1% of mid, divided by notional per 1% in the
    1-5% ring. A value well above 1 means liquidity is piled up close to price on that side (a
    "wall" nearby); below 1 means the near book is thin. Columns bid_near_ratio, ask_near_ratio.
    Much blunter than find_walls on L2 levels; only a stand-in until the VM collector has history.
    """
    p = df.pivot_table(index="timestamp", columns="percentage", values="notional", aggfunc="last")
    out = pd.DataFrame(index=p.index)
    for name, sgn in (("bid", -1), ("ask", 1)):
        near = p[sgn * 1.0].to_numpy()
        outer = (p[sgn * 5.0].to_numpy() - near) / 4.0
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"{name}_near_ratio"] = np.where(outer > 0, near / outer, np.nan)
    return out


# ---------------------------------------------------------------- VM collector snapshots


def snapshot_features(rec: dict[str, Any], *, top_n: int = 20, band_pct: float = 0.1, wall_k: float = 5.0) -> dict[str, Any]:
    """Features of one VM collector record (scripts/orderbook_collect.py line)."""
    bids, asks, mid = rec["b"], rec["a"], float(rec["mid"])
    walls = find_walls(bids, asks, mid, k=wall_k)
    return {
        "t": pd.Timestamp(rec["t"], unit="ms", tz="UTC"),
        "mid": mid,
        "obi5": obi_levels(bids, asks, 5),
        f"obi{top_n}": obi_levels(bids, asks, top_n),
        "obi_band": obi_band(bids, asks, mid, band_pct),
        "bid_wall_bps": nearest_wall_bps(walls, "bid"),
        "ask_wall_bps": nearest_wall_bps(walls, "ask"),
    }
