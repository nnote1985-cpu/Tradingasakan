"""Signals the agent book waits on, and the code that decides when one fires (docs/agent-book-plan.md §3).

The Decider may only ask for conditions from :data:`CONDITIONS`; it never
writes condition code. Code checks every active signal on each closed 1h
bar. Rules kept here:

- a signal lives at most :data:`MAX_TTL_MS` (72 h) and at most
  :data:`MAX_ACTIVE` signals are active per book;
- only bars that closed after the signal was created count, so a signal can
  never fire on data the Decider had already seen;
- a condition that lacks data (too few bars, no funding) does not fire and
  says so, it never guesses;
- when nothing is active, :func:`fallback_signal` adds a ``time_check`` 24 h
  out, so the book never goes silent.

Everything is Decimal; nothing here touches the database or calls a model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from trading.ingest.bars import Bar

HOUR_MS = 3_600_000
MAX_TTL_MS = 72 * HOUR_MS
MAX_ACTIVE = 5
FALLBACK_AFTER_MS = 24 * HOUR_MS
RSI_PERIOD = 14
VOLUME_LOOKBACK = 20
MAX_MOVE_HOURS = 72

# condition -> the parameters it takes (all Decimal, except time_check's "at_ms")
CONDITIONS: Mapping[str, tuple[str, ...]] = {
    "price_close_above": ("level",),
    "price_close_below": ("level",),
    "price_touch": ("level",),
    "rsi_below": ("value",),
    "rsi_above": ("value",),
    "move_pct": ("pct", "hours"),
    "volume_spike": ("multiple",),
    "funding_above": ("rate",),
    "funding_below": ("rate",),
    "time_check": ("at_ms",),
}


class Status(StrEnum):
    ACTIVE = "ACTIVE"
    FIRED = "FIRED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Signal:
    id: str
    condition: str
    params: Mapping[str, Decimal]
    created_at_ms: int
    expires_at_ms: int
    status: Status = Status.ACTIVE


@dataclass(frozen=True)
class Evaluation:
    fired: bool
    reason: str             # FIRED, NOT_MET, NO_NEW_BAR, INSUFFICIENT_DATA, NO_FUNDING, EXPIRED, NOT_ACTIVE


def _dec(v: object) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def parse_params(condition: str, raw: Mapping[str, object]) -> tuple[dict[str, Decimal], list[str]]:
    """Turn the Decider's raw parameters into Decimals; the second value lists what is wrong (empty if valid)."""
    if condition not in CONDITIONS:
        return {}, [f"UNKNOWN_CONDITION:{condition}"]
    want = CONDITIONS[condition]
    problems = [f"UNEXPECTED_PARAM:{k}" for k in raw if k not in want]
    params: dict[str, Decimal] = {}
    for k in want:
        d = _dec(raw.get(k))
        if d is None:
            problems.append(f"BAD_PARAM:{k}")
        else:
            params[k] = d
    if problems:
        return params, problems
    if condition.startswith("price_") and params["level"] <= 0:
        problems.append("LEVEL_NOT_POSITIVE")
    if condition.startswith("rsi_") and not 0 < params["value"] < 100:
        problems.append("RSI_OUT_OF_RANGE")
    if condition == "move_pct":
        h = params["hours"]
        if params["pct"] == 0:
            problems.append("PCT_ZERO")
        if h != h.to_integral_value() or not 1 <= h <= MAX_MOVE_HOURS:
            problems.append("HOURS_OUT_OF_RANGE")
    if condition == "volume_spike" and params["multiple"] <= 1:
        problems.append("MULTIPLE_NOT_ABOVE_1")
    if condition == "time_check" and params["at_ms"] != params["at_ms"].to_integral_value():
        problems.append("AT_NOT_WHOLE_MS")
    return params, problems


def check_new(condition: str, params: Mapping[str, Decimal], now_ms: int, expires_at_ms: int, active: int) -> list[str]:
    """Why a new signal would be refused (empty if it may be stored). ``active`` = the book's active signals now."""
    problems = []
    if active >= MAX_ACTIVE:
        problems.append("TOO_MANY_ACTIVE")
    if not now_ms < expires_at_ms <= now_ms + MAX_TTL_MS:
        problems.append("EXPIRY_OUT_OF_RANGE")
    if condition == "time_check" and "at_ms" in params and not now_ms < int(params["at_ms"]) <= expires_at_ms:
        problems.append("TIME_CHECK_OUTSIDE_LIFETIME")
    return problems


def rsi(closes: Sequence[Decimal], period: int = RSI_PERIOD) -> Decimal | None:
    """Wilder RSI of the last close; None with fewer than ``period`` + 1 closes."""
    if len(closes) < period + 1:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum((d for d in diffs[:period] if d > 0), Decimal(0)) / period
    loss = sum((-d for d in diffs[:period] if d < 0), Decimal(0)) / period
    for d in diffs[period:]:
        gain = (gain * (period - 1) + max(d, Decimal(0))) / period
        loss = (loss * (period - 1) + max(-d, Decimal(0))) / period
    if loss == 0:
        return Decimal(100)
    return 100 - 100 / (1 + gain / loss)


def evaluate(signal: Signal, bars: Sequence[Bar], now_ms: int, funding_rate: Decimal | None = None) -> Evaluation:
    """Does ``signal`` fire on the last closed 1h bar in ``bars`` (oldest first, closed only)?"""
    if signal.status is not Status.ACTIVE:
        return Evaluation(False, "NOT_ACTIVE")
    if now_ms >= signal.expires_at_ms:
        return Evaluation(False, "EXPIRED")
    if not bars or bars[-1].interval != "1h":
        return Evaluation(False, "INSUFFICIENT_DATA")
    last = bars[-1]
    if last.close_time <= signal.created_at_ms:
        return Evaluation(False, "NO_NEW_BAR")
    p, c = signal.params, signal.condition

    def done(ok: bool) -> Evaluation:
        return Evaluation(ok, "FIRED" if ok else "NOT_MET")

    if c == "price_close_above":
        return done(last.close > p["level"])
    if c == "price_close_below":
        return done(last.close < p["level"])
    if c == "price_touch":
        return done(last.low <= p["level"] <= last.high)
    if c in ("rsi_below", "rsi_above"):
        r = rsi([b.close for b in bars])
        if r is None:
            return Evaluation(False, "INSUFFICIENT_DATA")
        return done(r < p["value"] if c == "rsi_below" else r > p["value"])
    if c == "move_pct":
        h = int(p["hours"])
        if len(bars) < h + 1:
            return Evaluation(False, "INSUFFICIENT_DATA")
        move = (last.close / bars[-1 - h].close - 1) * 100
        return done(move <= p["pct"] if p["pct"] < 0 else move >= p["pct"])
    if c == "volume_spike":
        prior = bars[-1 - VOLUME_LOOKBACK:-1]
        if len(prior) < VOLUME_LOOKBACK:
            return Evaluation(False, "INSUFFICIENT_DATA")
        mean = sum((b.volume for b in prior), Decimal(0)) / VOLUME_LOOKBACK
        return done(mean > 0 and last.volume >= p["multiple"] * mean)
    if c in ("funding_above", "funding_below"):
        if funding_rate is None:
            return Evaluation(False, "NO_FUNDING")
        return done(funding_rate > p["rate"] if c == "funding_above" else funding_rate < p["rate"])
    if c == "time_check":
        return done(last.close_time >= int(p["at_ms"]))
    raise ValueError(f"unknown condition {c}")


def expired(signals: Sequence[Signal], now_ms: int) -> list[str]:
    """Ids of active signals whose lifetime has ended."""
    return [s.id for s in signals if s.status is Status.ACTIVE and now_ms >= s.expires_at_ms]


def fallback_signal(signals: Sequence[Signal], now_ms: int, new_id: str) -> Signal | None:
    """A ``time_check`` 24 h out when the book has no active signal left; None otherwise."""
    if any(s.status is Status.ACTIVE and now_ms < s.expires_at_ms for s in signals):
        return None
    at = now_ms + FALLBACK_AFTER_MS
    return Signal(new_id, "time_check", {"at_ms": Decimal(at)}, now_ms, at + HOUR_MS)
