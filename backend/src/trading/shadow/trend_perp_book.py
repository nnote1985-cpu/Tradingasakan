"""futures-r7: R7 (daily SMA100 long/short, BTC perp, 1x) as a forward SHADOW book.

docs/futures-r7-book-plan.md. The rules are R7's (docs/research/r7-futures-longshort-plan.md);
the book is the Decimal perp journal ``ShadowLedger`` with the spot.v1 loss levels.
Equity is marked at each daily close, funding is booked there from the day's summed
rate, and the limits are checked there. A HALT closes the position at the open of the
first 1h bar after the close that tripped it; only a resume record clears it.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from trading.risk.profiles import SPOT_V1
from trading.risk.sizing import RiskProfile
from trading.shadow.ledger import D, ShadowLedger
from trading.shadow.risk_state import RiskState

RULES_VERSION = "futures-r7"
BOOK_FORMAT = "trend_perp_book.v1"
START_EQUITY = Decimal("10000")
SMA_N = 100
FEE = Decimal("0.0005")              # perp taker, per side
BH_FEE = Decimal("0.0010")           # the benchmark owner buys spot-style once
SLIP_BPS = Decimal("2")
QTY_Q = Decimal("1e-12")
CENT = Decimal("0.00000001")


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _px(x) -> Decimal:
    return D(str(float(x)))


def side_signal(daily_close: pd.Series) -> pd.Series:
    """+1 / -1 / 0 at each close: R7's rule (invalid or equal days keep the previous side)."""
    sma = daily_close.rolling(SMA_N, min_periods=SMA_N).mean()
    raw = pd.Series(np.nan, index=daily_close.index)
    ok = sma.notna() & daily_close.notna()
    raw[ok & (daily_close > sma)] = 1.0
    raw[ok & (daily_close < sma)] = -1.0
    return raw.ffill().fillna(0.0).astype(int)


class TrendPerpBook:
    rules_version = RULES_VERSION        # subclasses with other rules override this

    def __init__(self, state: dict | None = None, profile: RiskProfile = SPOT_V1):
        self.profile = profile
        if state is None:
            self.book, self.bh = ShadowLedger(), ShadowLedger()
            self.risk = RiskState()
            self.last_day: str | None = None
            self.side = 0
            self.pending: dict | None = None
            self.history: list[dict] = []
            self.trades: list[dict] = []
            self.journal: list[dict] = []
            self.bh_filled = False
            self.start_equity: str | None = None
        else:
            if state.get("format") != BOOK_FORMAT or state.get("rules_version") != self.rules_version:
                raise ValueError("state belongs to another book format or rules version")
            if state.get("profile") != self._profile_dict():
                raise ValueError("state was written with other loss limits")
            self.book, self.bh = ShadowLedger.from_dict(state["book"]), ShadowLedger.from_dict(state["buy_and_hold"])
            self.risk = RiskState.from_dict(state["risk"])
            for k in ("last_day", "side", "pending", "history", "trades", "journal", "bh_filled", "start_equity"):
                setattr(self, k, state[k])

    def _profile_dict(self) -> dict:
        return {k: str(v) for k, v in asdict(self.profile).items()}

    def to_dict(self) -> dict:
        return {"format": BOOK_FORMAT, "rules_version": self.rules_version, "profile": self._profile_dict(),
                "book": self.book.to_dict(), "buy_and_hold": self.bh.to_dict(), "risk": self.risk.to_dict(),
                "last_day": self.last_day, "side": self.side, "pending": self.pending, "history": self.history,
                "trades": self.trades, "journal": self.journal, "bh_filled": self.bh_filled,
                "start_equity": self.start_equity}

    # ---- decisions -------------------------------------------------------------
    @staticmethod
    def _order(decided_on: str, side: int, data_ready_at, decided_at, notes: list[str], kind: str = "decision") -> dict:
        ready, done = _ts(data_ready_at), _ts(decided_at)
        if done < ready:
            raise ValueError("decided_at is earlier than data_ready_at")
        return {"kind": kind, "decided_on": decided_on, "side": side, "data_ready_at": ready.isoformat(),
                "decided_at": done.isoformat(), "fill_at": (done.floor("1h") + pd.Timedelta(hours=1)).isoformat(),
                "notes": notes}

    def open(self, decided_on: str, side: int, data_ready_at: datetime, decided_at: datetime,
             notes: list[str], equity=START_EQUITY) -> dict:
        if self.last_day is not None:
            raise ValueError("book already open")
        at = _ts(decided_at)
        self.book.open("open", at, equity)
        self.bh.open("open", at, equity)
        self.last_day, self.start_equity = decided_on, str(D(equity))
        self.pending = self._order(decided_on, side, data_ready_at, decided_at, notes)
        self.journal.append({**self.pending, "kind": "open"})
        return self.journal[-1]

    def decide(self, decided_on: str, side: int | None, data_ready_at: datetime, decided_at: datetime,
               notes: list[str]) -> dict | None:
        """Record the decision on a close the book has reached. ``side`` None: not enough data."""
        if decided_on != self.last_day:
            return None
        if any(e.get("decided_on") == decided_on and e["kind"] in ("open", "decision", "no_decision", "ignored_halted")
               for e in self.journal):
            return None
        stamp = {"decided_on": decided_on, "data_ready_at": _ts(data_ready_at).isoformat(),
                 "decided_at": _ts(decided_at).isoformat(), "notes": notes}
        if self.risk.halted:
            self.journal.append({"kind": "ignored_halted", "side": side, **stamp})
            return self.journal[-1]
        if side is None:
            self.journal.append({"kind": "no_decision", "side": self.side, **stamp})
            return self.journal[-1]
        current = (self.pending or {}).get("side", self.side)
        if side == current:
            return None
        self.pending = self._order(decided_on, side, data_ready_at, decided_at, notes)
        self.journal.append(dict(self.pending))
        return self.journal[-1]

    def resume(self, by: str, reason: str, at: datetime) -> dict:
        if not self.risk.halted:
            raise ValueError("book is not halted")
        if not by.strip() or not reason.strip():
            raise ValueError("a resume needs the name of the person and a reason")
        mark = _px(self.history[-1]["close"]) if self.history else Decimal(1)
        self.risk.halted, self.risk.halt_reason, self.risk.halted_at = False, None, None
        self.risk.high_water_mark = self.book.economic_equity(mark)
        self.risk.drawdown_alert_armed = True
        self.journal.append({"kind": "resume", "by": by, "reason": reason, "at": _ts(at).isoformat(),
                             "equity": str(self.book.trading_equity(mark))})
        return self.journal[-1]

    # ---- fills and marks -----------------------------------------------------
    @staticmethod
    def _go(led: ShadowLedger, key: str, at: pd.Timestamp, side: int, o: Decimal, fee_rate: Decimal) -> list[dict]:
        """Move ``led`` to ``side`` at 1x: close what is open, then open the new side."""
        out = []
        pos = led.position.qty
        held = 1 if pos > 0 else -1 if pos < 0 else 0
        if held == side:
            return out
        slip = SLIP_BPS / 10000
        if held:
            px = o * (1 - held * slip)
            qty = abs(pos)
            fee = (qty * px * fee_rate).quantize(CENT)
            led.fill(f"{key}:close", at, "SELL" if held > 0 else "BUY", qty, px, fee, reference_price=o)
            out.append({"action": "close", "side": "SELL" if held > 0 else "BUY", "qty": str(qty), "price": str(px),
                        "fee": str(fee)})
        if side:
            px = o * (1 + side * slip)
            eq = led.bal("cash")
            qty = (eq / px).quantize(QTY_Q, rounding=ROUND_FLOOR)
            fee = (qty * px * fee_rate).quantize(CENT)
            led.fill(f"{key}:open", at, "BUY" if side > 0 else "SELL", qty, px, fee, reference_price=o)
            out.append({"action": "open", "side": "BUY" if side > 0 else "SELL", "qty": str(qty), "price": str(px),
                        "fee": str(fee)})
        return out

    def advance(self, daily: pd.DataFrame, hourly: pd.DataFrame, funding: pd.Series,
                funding_estimated: pd.Series | None = None) -> tuple[list[dict], list[str]]:
        """Fill the pending order once its 1h bar exists, book each day's funding and mark
        every newly closed day; check the loss limits at each close."""
        if self.last_day is None:
            raise ValueError("book is not open")
        added: list[dict] = []
        problems: list[str] = []

        def log(e: dict) -> None:
            added.append(e)
            self.journal.append(e)
        day = _ts(self.last_day) + pd.Timedelta(days=1)
        while day in daily.index:
            c = float(daily.loc[day, "close"])
            p = self.pending
            if p and _ts(p["fill_at"]) < day + pd.Timedelta(days=1):
                fill_at = _ts(p["fill_at"])
                if fill_at <= _ts(p["decided_at"]):
                    problems.append(f"simulator error: fill {fill_at.isoformat()} is not after {p['decided_at']}")
                    break
                if fill_at not in hourly.index or not math.isfinite(float(hourly.loc[fill_at, "open"])):
                    problems.append(f"no 1h bar at {fill_at.isoformat()}: the order cannot fill yet")
                    break
                o = _px(hourly.loc[fill_at, "open"])
                for t in self._go(self.book, f"fill:{p['decided_on']}:{p['kind']}", fill_at, p["side"], o, FEE):
                    self.trades.append({"date": str(day.date()), "at": fill_at.isoformat(), "book": "paper",
                                        "decided_on": p["decided_on"], "decided_at": p["decided_at"],
                                        "reason": p["kind"], **t})
                    log({"kind": "trade", "date": str(day.date()), "at": fill_at.isoformat(), "target_side": p["side"],
                         "reason": p["kind"], "decided_on": p["decided_on"], "decided_at": p["decided_at"], **t})
                if not self.bh_filled:
                    for t in self._go(self.bh, "fill:buy_and_hold", fill_at, 1, o, BH_FEE):
                        self.trades.append({"date": str(day.date()), "at": fill_at.isoformat(), "book": "buy_and_hold", **t})
                    self.bh_filled = True
                self.side = p["side"]
                self.pending = None
            if not math.isfinite(c):
                problems.append(f"no close for {day.date()}")
                break
            mark = _px(c)
            close_at = day + pd.Timedelta(days=1)
            if day in funding.index and self.book.position.qty != 0:
                est = bool(funding_estimated.get(day, False)) if funding_estimated is not None else False
                if self.book.funding(f"funding:{day.date()}", close_at, _px(funding.loc[day]), mark):
                    self.book.entries[-1].data["estimated"] = est
            if self.history:
                prev_tr, prev_ec = D(self.history[-1]["equity"]), D(self.history[-1]["economic_equity"])
            else:
                prev_tr = prev_ec = D(self.start_equity)
            self.risk.start_day(day, prev_tr, prev_ec)
            tr, ec = self.book.trading_equity(mark), self.book.economic_equity(mark)
            n_alerts = len(self.risk.alerts)
            reason = self.risk.observe(close_at, tr, ec, self.profile)
            for a in self.risk.alerts[n_alerts:]:
                log({"kind": "alert", "date": str(day.date()), **a})
            if reason:
                halt = self._order(str(day.date()), 0, close_at, close_at,
                                   [f"HALT {reason}: close the position and wait for a resume"], kind="halt")
                self.pending = halt
                log({**halt, "reason": reason, "trading_equity": str(tr), "high_water_mark": str(self.risk.high_water_mark)})
            qty = self.book.position.qty
            self.history.append({"date": str(day.date()), "close": c, "side_held_at_close": self.side,
                                 "equity": str(tr), "economic_equity": str(ec), "exposure": str(abs(qty) * mark),
                                 "buy_and_hold": str(self.bh.trading_equity(mark)), "halted": self.risk.halted})
            self.last_day = str(day.date())
            day += pd.Timedelta(days=1)
        later = daily.index[daily.index > day]
        if len(later) and day not in daily.index:
            problems.append(f"{day.date()} is missing but later days exist: the book waits for it")
        return added, problems

    def performance(self) -> dict:
        s0 = float(self.start_equity or START_EQUITY)
        if not self.history:
            return {"days": 0, "equity": s0, "buy_and_hold": s0, "halted": self.risk.halted}
        eq = pd.Series([s0] + [float(r["equity"]) for r in self.history])
        bh = pd.Series([s0] + [float(r["buy_and_hold"]) for r in self.history])

        def dd(x):
            return float((1 - x / x.cummax()).max())
        return {"days": len(self.history), "since": self.history[0]["date"], "through": self.history[-1]["date"],
                "equity": float(eq.iloc[-1]), "buy_and_hold": float(bh.iloc[-1]),
                "return": float(eq.iloc[-1] / s0 - 1), "bh_return": float(bh.iloc[-1] / s0 - 1),
                "max_dd": dd(eq), "bh_max_dd": dd(bh), "side_held": self.side,
                "funding_paid": str(self.book.bal("funding")), "fees": str(self.book.bal("fees")),
                "trades": sum(1 for t in self.trades if t["book"] == "paper"),
                "alerts": len(self.risk.alerts), "halted": self.risk.halted, "halt_reason": self.risk.halt_reason,
                "ledger_problems": self.book.check() + self.bh.check()}
