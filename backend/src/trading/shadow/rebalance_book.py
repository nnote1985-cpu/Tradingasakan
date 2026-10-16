"""pathA-v1.2: pathA's daily BTC weights on the Decimal spot journal (docs/pathA-v1.2-plan.md).

Same decisions, timing and costs as the pathA-v1.1 float book
(``trading.paper.ledger``); the differences are the journal (``SpotLedger``) and
the ``spot.v1`` loss limits. Equity is marked at each daily close and the limits
are checked there. A HALT sells all BTC at the open of the first 1h bar after the
close that tripped it, later decisions are recorded as ignored, and only a
resume record with a name and reason clears it.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

import pandas as pd

from trading.risk.profiles import SPOT_V1
from trading.risk.sizing import RiskProfile
from trading.shadow.ledger import D
from trading.shadow.risk_state import RiskState
from trading.shadow.spot_ledger import SpotLedger, money, money_floor

RULES_VERSION = "pathA-v1.2"
BOOK_FORMAT = "rebalance_book.v1"
START_EQUITY = Decimal("10000")
FEE = Decimal("0.0010")              # spot taker, per side (RegimeCosts)
SLIP_BPS = Decimal("2")
QTY_Q = Decimal("1e-12")             # BTC quantities in the research book


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _px(x) -> Decimal:
    return D(str(float(x)))


class PathABook:
    def __init__(self, state: dict | None = None, profile: RiskProfile = SPOT_V1):
        self.profile = profile
        if state is None:
            self.book, self.bh = SpotLedger(), SpotLedger()
            self.risk = RiskState()
            self.last_day: str | None = None
            self.weight = 0.0
            self.pending: dict | None = None
            self.history: list[dict] = []
            self.trades: list[dict] = []
            self.journal: list[dict] = []
            self.bh_filled = False
            self.start_equity: str | None = None
        else:
            if state.get("format") != BOOK_FORMAT or state.get("rules_version") != RULES_VERSION:
                raise ValueError("state belongs to another book format or rules version")
            if state.get("profile") != self._profile_dict():
                raise ValueError("state was written with other loss limits")
            self.book, self.bh = SpotLedger.from_dict(state["book"]), SpotLedger.from_dict(state["buy_and_hold"])
            self.risk = RiskState.from_dict(state["risk"])
            for k in ("last_day", "weight", "pending", "history", "trades", "journal", "bh_filled", "start_equity"):
                setattr(self, k, state[k])

    def _profile_dict(self) -> dict:
        return {k: str(v) for k, v in asdict(self.profile).items()}

    def to_dict(self) -> dict:
        return {"format": BOOK_FORMAT, "rules_version": RULES_VERSION, "profile": self._profile_dict(),
                "book": self.book.to_dict(), "buy_and_hold": self.bh.to_dict(), "risk": self.risk.to_dict(),
                "last_day": self.last_day, "weight": self.weight, "pending": self.pending,
                "history": self.history, "trades": self.trades, "journal": self.journal,
                "bh_filled": self.bh_filled, "start_equity": self.start_equity}

    # ---- decisions -------------------------------------------------------------
    @staticmethod
    def _order(decided_on: str, weight: float, data_ready_at, decided_at, notes: list[str],
               kind: str = "decision") -> dict:
        ready, done = _ts(data_ready_at), _ts(decided_at)
        if done < ready:
            raise ValueError("decided_at is earlier than data_ready_at")
        return {"kind": kind, "decided_on": decided_on, "weight": weight, "data_ready_at": ready.isoformat(),
                "decided_at": done.isoformat(), "fill_at": (done.floor("1h") + pd.Timedelta(hours=1)).isoformat(),
                "notes": notes}

    def open(self, decided_on: str, weight: float, data_ready_at: datetime, decided_at: datetime,
             notes: list[str], equity=START_EQUITY) -> dict:
        """Open from cash on the close of ``decided_on`` (as ``trading.paper.ledger.new_state``)."""
        if self.last_day is not None:
            raise ValueError("book already open")
        at = _ts(decided_at)
        self.book.open("open", at, equity)
        self.bh.open("open", at, equity)
        self.last_day = decided_on
        self.start_equity = str(D(equity))
        self.pending = self._order(decided_on, weight, data_ready_at, decided_at, notes)
        self.journal.append({**self.pending, "kind": "open"})
        return self.journal[-1]

    def decide(self, decided_on: str, weight: float | None, data_ready_at: datetime, decided_at: datetime,
               notes: list[str]) -> dict | None:
        """Record the decision on a close the book has reached. ``weight`` None: not enough data."""
        if decided_on != self.last_day:
            return None
        if any(e.get("decided_on") == decided_on and e["kind"] in ("open", "decision", "no_decision", "ignored_halted")
               for e in self.journal):
            return None                                        # already decided on this close
        stamp = {"decided_on": decided_on, "data_ready_at": _ts(data_ready_at).isoformat(),
                 "decided_at": _ts(decided_at).isoformat(), "notes": notes}
        if self.risk.halted:
            self.journal.append({"kind": "ignored_halted", "weight": weight, **stamp})
            return self.journal[-1]
        if weight is None:
            self.journal.append({"kind": "no_decision", "weight": self.weight, **stamp})
            return self.journal[-1]
        current = (self.pending or {}).get("weight", self.weight)
        if weight == current:
            return None
        self.pending = self._order(decided_on, weight, data_ready_at, decided_at, notes)
        self.journal.append(dict(self.pending))
        return self.journal[-1]

    def resume(self, by: str, reason: str, at: datetime) -> dict:
        """Clear a HALT. The high-water mark restarts from the equity now."""
        if not self.risk.halted:
            raise ValueError("book is not halted")
        if not by.strip() or not reason.strip():
            raise ValueError("a resume needs the name of the person and a reason")
        mark = _px(self.history[-1]["close"]) if self.history else Decimal(1)
        eq = self.book.trading_equity(mark)
        self.risk.halted, self.risk.halt_reason, self.risk.halted_at = False, None, None
        self.risk.high_water_mark = self.book.economic_equity(mark)
        self.risk.drawdown_alert_armed = True
        self.journal.append({"kind": "resume", "by": by, "reason": reason, "at": _ts(at).isoformat(),
                             "equity": str(eq)})
        return self.journal[-1]

    # ---- fills and marks -----------------------------------------------------
    def _rebalance(self, led: SpotLedger, key: str, at: pd.Timestamp, weight: float, o: Decimal) -> dict | None:
        btc, cash = led.position.qty, led.bal("cash")
        eq_open = cash + btc * o
        delta = D(str(weight)) * eq_open - btc * o
        if abs(delta) <= Decimal("1e-9") * eq_open:
            return None
        if delta > 0:
            px = o * (1 + SLIP_BPS / 10000)
            spend = money_floor(min(delta, cash))
            qty = (spend * (1 - FEE) / px).quantize(QTY_Q, rounding=ROUND_FLOOR)
            fee = spend - money(qty * px)                       # cost + fee is exactly the cash spent
            led.fill(key, at, "BUY", qty, px, fee, reference_price=o)
            return {"side": "BUY", "price": str(px), "qty": str(qty), "value": str(spend), "fee": str(fee)}
        px = o * (1 - SLIP_BPS / 10000)
        qty = min((-delta / o).quantize(QTY_Q, rounding=ROUND_FLOOR), btc)
        if abs(qty - btc) <= QTY_Q:
            qty = btc                                           # selling (almost) everything sells all of it
        fee = money(qty * px * FEE)
        led.fill(key, at, "SELL", qty, px, fee, reference_price=o)
        return {"side": "SELL", "price": str(px), "qty": str(qty), "value": str(money(qty * px) - fee), "fee": str(fee)}

    def advance(self, daily: pd.DataFrame, hourly: pd.DataFrame) -> tuple[list[dict], list[str]]:
        """Fill the pending order once its 1h bar exists and mark every newly closed
        day; check the loss limits at each close. Returns (entries added, problems)."""
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
                t = self._rebalance(self.book, f"fill:{p['decided_on']}:{p['kind']}", fill_at, p["weight"], o)
                if t:
                    self.trades.append({"date": str(day.date()), "at": fill_at.isoformat(), "book": "paper",
                                        "decided_on": p["decided_on"], "decided_at": p["decided_at"],
                                        "reason": p["kind"], **t})
                    log({"kind": "trade", "date": str(day.date()), "at": fill_at.isoformat(),
                                  "weight": p["weight"], "reason": p["kind"], **t})
                if not self.bh_filled:
                    tb = self._rebalance(self.bh, "fill:buy_and_hold", fill_at, 1.0, o)
                    if tb:
                        self.trades.append({"date": str(day.date()), "at": fill_at.isoformat(),
                                            "book": "buy_and_hold", **tb})
                    self.bh_filled = True
                self.weight = p["weight"]
                self.pending = None
            if not math.isfinite(c):
                problems.append(f"no close for {day.date()}")
                break
            mark = _px(c)
            close_at = day + pd.Timedelta(days=1)
            if self.history:   # day start = equity carried in from the previous close
                prev_tr, prev_ec = D(self.history[-1]["equity"]), D(self.history[-1]["economic_equity"])
            else:
                prev_tr = prev_ec = D(self.start_equity)
            self.risk.start_day(day, prev_tr, prev_ec)
            tr, ec = self.book.trading_equity(mark), self.book.economic_equity(mark)
            n_alerts = len(self.risk.alerts)
            reason = self.risk.observe(close_at, tr, ec, self.profile)   # stamped at the close
            for a in self.risk.alerts[n_alerts:]:
                log({"kind": "alert", "date": str(day.date()), **a})
            if reason:
                halt = self._order(str(day.date()), 0.0, close_at, close_at,
                                   [f"HALT {reason}: sell all BTC and wait for a resume"], kind="halt")
                self.pending = halt
                log({**halt, "reason": reason, "trading_equity": str(tr),
                     "high_water_mark": str(self.risk.high_water_mark)})
            self.history.append({"date": str(day.date()), "close": c, "weight_held_at_close": self.weight,
                                 "equity": str(tr), "economic_equity": str(ec),
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
                "max_dd": dd(eq), "bh_max_dd": dd(bh), "weight_held": self.weight,
                "trades": sum(1 for t in self.trades if t["book"] == "paper"),
                "alerts": len(self.risk.alerts), "halted": self.risk.halted, "halt_reason": self.risk.halt_reason,
                "ledger_problems": self.book.check() + self.bh.check()}
