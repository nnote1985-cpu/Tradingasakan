"""pathA-r10a-hedge ("3a"): R10a on spot, hedged with a perp short instead of selling (docs/r10a-hedge-book-plan.md).

Targets are fractions of total equity E: spot w + 0.5(1 - w), perp -0.5(1 - w), where w is R10a's
weight. Every daily run leaves a check that fills at the first 1h bar after the decision; there each
leg (perp first, then spot) trades to its target when the target changed or the leg drifted more than
max(20% of target, 5% of E), as R15's ``simulate``. Spot and perp are two Decimal ledgers joined by
transfer entries. A short carries a resting stop at fill price + 3 x ATR14, simulated on 1h highs.
Equity is marked at each daily close (spot close + perp close), funding is booked there, and the
``spot.v1`` limits are checked on the combined equity. A HALT closes both legs at the next 1h open
and only a resume record clears it.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal

import pandas as pd

from trading.risk.profiles import SPOT_V1
from trading.risk.sizing import RiskProfile
from trading.shadow.ledger import ZERO, D, ShadowLedger
from trading.shadow.risk_state import RiskState
from trading.shadow.spot_ledger import SpotLedger, money

RULES_VERSION = "pathA-r10a-hedge"
BOOK_FORMAT = "hedge_book.v1"
START_EQUITY = Decimal("10000")
SPOT_FEE = Decimal("0.0010")
PERP_FEE = Decimal("0.0005")
SLIP = Decimal("2") / Decimal("10000")
REB_REL, REB_MIN = Decimal("0.20"), Decimal("0.05")
MAINT = Decimal("0.005")
QTY_Q = Decimal("1e-12")
DECISION_KINDS = ("open", "decision", "no_decision", "ignored_halted")


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _px(x) -> Decimal:
    return D(str(float(x)))


def targets(weight: float) -> tuple[Decimal, Decimal]:
    """(spot, perp) fractions of equity for R10a weight ``weight``."""
    w = D(str(weight))
    idle = 1 - w
    return w + idle / 2, -idle / 2


class HedgeBook:
    rules_version = RULES_VERSION
    stop_atr: Decimal | None = Decimal("3")     # stop distance in ATR14; None turns the stop off (tests)

    def __init__(self, state: dict | None = None, profile: RiskProfile = SPOT_V1):
        self.profile = profile
        if state is None:
            self.spot, self.perp, self.bh = SpotLedger(), ShadowLedger(), SpotLedger()
            self.risk = RiskState()
            self.last_day: str | None = None
            self.weight = 0.0
            self.pending: dict | None = None
            self.prev_targets: list[str] | None = None
            self.stop: dict | None = None
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
            self.spot, self.bh = SpotLedger.from_dict(state["spot"]), SpotLedger.from_dict(state["buy_and_hold"])
            self.perp = ShadowLedger.from_dict(state["perp"])
            self.risk = RiskState.from_dict(state["risk"])
            for k in ("last_day", "weight", "pending", "prev_targets", "stop", "history", "trades", "journal",
                      "bh_filled", "start_equity"):
                setattr(self, k, state[k])

    def _profile_dict(self) -> dict:
        return {k: str(v) for k, v in asdict(self.profile).items()}

    def to_dict(self) -> dict:
        return {"format": BOOK_FORMAT, "rules_version": self.rules_version, "profile": self._profile_dict(),
                "spot": self.spot.to_dict(), "perp": self.perp.to_dict(), "buy_and_hold": self.bh.to_dict(),
                "risk": self.risk.to_dict(), "last_day": self.last_day, "weight": self.weight,
                "pending": self.pending, "prev_targets": self.prev_targets, "stop": self.stop,
                "history": self.history, "trades": self.trades, "journal": self.journal,
                "bh_filled": self.bh_filled, "start_equity": self.start_equity}

    # ---- equity ----------------------------------------------------------------
    def equity(self, spot_px: Decimal, perp_px: Decimal) -> Decimal:
        return self.spot.trading_equity(spot_px) + self.perp.trading_equity(perp_px)

    def economic_equity(self, spot_px: Decimal, perp_px: Decimal) -> Decimal:
        return self.spot.economic_equity(spot_px) + self.perp.economic_equity(perp_px)

    def liquidation_price(self) -> Decimal | None:
        """Perp price at which the perp wallet alone falls to maintenance margin (1x isolated)."""
        q = self.perp.position.qty
        if q >= 0:
            return None
        a = -q
        return (self.perp.bal("cash") + a * self.perp.position.avg_entry) / (a * (1 + MAINT))

    # ---- decisions -------------------------------------------------------------
    @staticmethod
    def _order(decided_on: str, weight: float, data_ready_at, decided_at, notes: list[str], atr14,
               kind: str = "decision") -> dict:
        ready, done = _ts(data_ready_at), _ts(decided_at)
        if done < ready:
            raise ValueError("decided_at is earlier than data_ready_at")
        return {"kind": kind, "decided_on": decided_on, "weight": weight, "data_ready_at": ready.isoformat(),
                "decided_at": done.isoformat(), "fill_at": (done.floor("1h") + pd.Timedelta(hours=1)).isoformat(),
                "atr14": None if atr14 is None else str(_px(atr14)), "notes": notes}

    def open(self, decided_on: str, weight: float, data_ready_at: datetime, decided_at: datetime,
             notes: list[str], atr14, equity=START_EQUITY) -> dict:
        if self.last_day is not None:
            raise ValueError("book already open")
        at = _ts(decided_at)
        self.spot.open("spot:open", at, equity)
        self.bh.open("open", at, equity)
        self.last_day, self.start_equity = decided_on, str(D(equity))
        self.pending = self._order(decided_on, weight, data_ready_at, decided_at, notes, atr14)
        self.journal.append({**self.pending, "kind": "open"})
        return self.journal[-1]

    def decide(self, decided_on: str, weight: float | None, data_ready_at: datetime, decided_at: datetime,
               notes: list[str], atr14) -> dict | None:
        """Record the decision on a close the book has reached and leave a check for the next 1h bar.
        ``weight`` None: not enough data (the check keeps the current weight). Returns the journal
        entry when there is one; a plain check is not journaled."""
        if decided_on != self.last_day:
            return None
        if (self.pending and self.pending["decided_on"] == decided_on) or any(
                e.get("decided_on") == decided_on and e["kind"] in DECISION_KINDS for e in self.journal):
            return None
        stamp = {"decided_on": decided_on, "data_ready_at": _ts(data_ready_at).isoformat(),
                 "decided_at": _ts(decided_at).isoformat(), "notes": notes}
        if self.risk.halted:
            self.journal.append({"kind": "ignored_halted", "weight": weight, **stamp})
            return self.journal[-1]
        current = (self.pending or {}).get("weight", self.weight)
        if self.pending and self.pending["kind"] == "halt":
            return None
        if weight is None:
            self.pending = self._order(decided_on, current, data_ready_at, decided_at, notes, atr14, kind="check")
            self.journal.append({"kind": "no_decision", "weight": current, **stamp})
            return self.journal[-1]
        if weight == current:
            self.pending = self._order(decided_on, weight, data_ready_at, decided_at, notes, atr14, kind="check")
            return None
        self.pending = self._order(decided_on, weight, data_ready_at, decided_at, notes, atr14)
        self.journal.append(dict(self.pending))
        return self.journal[-1]

    def resume(self, by: str, reason: str, at: datetime) -> dict:
        if not self.risk.halted:
            raise ValueError("book is not halted")
        if not by.strip() or not reason.strip():
            raise ValueError("a resume needs the name of the person and a reason")
        h = self.history[-1] if self.history else None
        sp, pp = (_px(h["close"]), _px(h["perp_close"])) if h else (Decimal(1), Decimal(1))
        self.risk.halted, self.risk.halt_reason, self.risk.halted_at = False, None, None
        self.risk.high_water_mark = self.economic_equity(sp, pp)
        self.risk.drawdown_alert_armed = True
        self.journal.append({"kind": "resume", "by": by, "reason": reason, "at": _ts(at).isoformat(),
                             "equity": str(self.equity(sp, pp))})
        return self.journal[-1]

    # ---- fills -------------------------------------------------------------------
    def _move(self, key: str, at: pd.Timestamp, to_spot: Decimal) -> None:
        """Move cash between the wallets: > 0 perp -> spot, < 0 spot -> perp."""
        a = money(to_spot, ROUND_DOWN)                 # toward zero: never more than the wallet holds
        if a == 0:
            return
        self.perp.transfer(f"perp:{key}:transfer", at, -a, "to spot" if a > 0 else "from spot")
        self.spot.transfer(f"spot:{key}:transfer", at, a, "from perp" if a > 0 else "to perp")

    def _perp_fill(self, key: str, at: pd.Timestamp, side: str, qty: Decimal, ref: Decimal, px: Decimal,
                   reason: str, log, extra: dict) -> dict:
        fee = money(qty * px * PERP_FEE)
        self.perp.fill(f"perp:{key}", at, side, qty, px, fee, reference_price=ref)
        t = {"leg": "perp", "side": side, "qty": str(qty), "price": str(px), "fee": str(fee)}
        self.trades.append({"date": str(at.date()), "at": at.isoformat(), "book": "paper", "reason": reason, **extra, **t})
        log({"kind": "trade", "date": str(at.date()), "at": at.isoformat(), "reason": reason, **extra, **t})
        return t

    def _spot_fill(self, key: str, at: pd.Timestamp, side: str, qty: Decimal, ref: Decimal, px: Decimal,
                   reason: str, log, extra: dict) -> None:
        fee = money(money(qty * px) * SPOT_FEE)
        self.spot.fill(f"spot:{key}", at, side, qty, px, fee, reference_price=ref)
        t = {"leg": "spot", "side": side, "qty": str(qty), "price": str(px), "fee": str(fee)}
        self.trades.append({"date": str(at.date()), "at": at.isoformat(), "book": "paper", "reason": reason, **extra, **t})
        log({"kind": "trade", "date": str(at.date()), "at": at.isoformat(), "reason": reason, **extra, **t})

    def _fill(self, p: dict, at: pd.Timestamp, so: Decimal, po: Decimal, log) -> None:
        key = f"fill:{p['decided_on']}:{p['kind']}"
        extra = {"decided_on": p["decided_on"], "decided_at": p["decided_at"], "weight": p["weight"]}
        halt = p["kind"] == "halt"
        ts, tp = (ZERO, ZERO) if halt else targets(p["weight"])
        eq = self.equity(so, po)
        prev = None if self.prev_targets is None else (D(self.prev_targets[0]), D(self.prev_targets[1]))
        cur_s, cur_p = self.spot.position.qty * so / eq, self.perp.position.qty * po / eq

        def go(cur: Decimal, tgt: Decimal, prv: Decimal | None) -> bool:
            if abs(cur - tgt) < Decimal("1e-12"):
                return False
            return halt or prv is None or tgt != prv or abs(cur - tgt) > max(REB_REL * abs(tgt), REB_MIN)

        dq_p = (tp * eq / po - self.perp.position.qty).quantize(QTY_Q, ROUND_DOWN) if go(cur_p, tp, prev and prev[1]) else ZERO
        dq_s = (ts * eq / so - self.spot.position.qty).quantize(QTY_Q, ROUND_DOWN) if go(cur_s, ts, prev and prev[0]) else ZERO
        if halt:
            dq_p, dq_s = -self.perp.position.qty, -self.spot.position.qty
        reason = p["kind"]
        perp_px = None
        if dq_p > 0:                                              # buy back short first: frees margin
            perp_px = po * (1 + SLIP)
            self._perp_fill(f"{key}:buy", at, "BUY", dq_p, po, perp_px, reason, log, extra)
        if dq_s < 0:
            q = min(-dq_s, self.spot.position.qty)
            self._spot_fill(f"{key}:sell", at, "SELL", q, so, so * (1 - SLIP), reason, log, extra)
        # The spot wallet keeps what its buy needs; the rest is perp margin (all of it to spot when no short).
        spx = so * (1 + SLIP)
        need = ZERO
        if dq_s > 0:
            cost = money(dq_s * spx)
            need = cost + money(cost * SPOT_FEE)
        short_after = self.perp.position.qty + (dq_p if dq_p < 0 else ZERO)
        if short_after == 0:
            self._move(key, at, self.perp.bal("cash"))
        else:
            t = need - self.spot.bal("cash")
            self._move(key, at, min(t, self.perp.bal("cash")) if t > 0 else t)
        if dq_p < 0:
            perp_px = po * (1 - SLIP)
            self._perp_fill(f"{key}:sell", at, "SELL", -dq_p, po, perp_px, reason, log, extra)
        if dq_s > 0:
            cash = self.spot.bal("cash")
            q = dq_s
            if money(q * spx) + money(money(q * spx) * SPOT_FEE) > cash:   # transfer was capped
                q = (cash / (spx * (1 + SPOT_FEE))).quantize(QTY_Q, ROUND_FLOOR)
            if q > 0:
                self._spot_fill(f"{key}:buy", at, "BUY", q, so, spx, reason, log, extra)
        if self.perp.position.qty >= 0:
            self.stop = None
        elif perp_px is not None:
            atr = p.get("atr14")
            if self.stop_atr is not None and atr is not None:
                self.stop = {"price": str(perp_px + self.stop_atr * D(atr)), "set_at": at.isoformat(), "atr14": atr,
                             "from_price": str(perp_px)}
            else:
                self.stop = None
        self.prev_targets = [str(ts), str(tp)]
        self.weight = 0.0 if halt else p["weight"]

    def _check_stop(self, bars: pd.DataFrame, log) -> None:
        if not self.stop or self.perp.position.qty >= 0:
            return
        level = D(self.stop["price"])
        for at, b in bars.iterrows():
            if _px(b["high"]) >= level:
                o = _px(b["open"])
                ref = max(level, o)
                q = -self.perp.position.qty
                self._perp_fill(f"stop:{_ts(at).isoformat()}", _ts(at), "BUY", q, ref, ref * (1 + SLIP), "stop", log,
                                {"stop": self.stop["price"]})
                self.stop = None
                return

    def advance(self, spot_daily: pd.DataFrame, spot_hourly: pd.DataFrame, perp_daily: pd.DataFrame,
                perp_hourly: pd.DataFrame, funding: pd.Series,
                funding_estimated: pd.Series | None = None) -> tuple[list[dict], list[str]]:
        """Fill the pending check once its 1h bars exist, run the perp stop on 1h highs, book funding
        and mark every day both markets have closed; check the loss limits at each close."""
        if self.last_day is None:
            raise ValueError("book is not open")
        added: list[dict] = []
        problems: list[str] = []

        def log(e: dict) -> None:
            added.append(e)
            self.journal.append(e)
        day = _ts(self.last_day) + pd.Timedelta(days=1)
        while day in spot_daily.index and day in perp_daily.index:
            nxt = day + pd.Timedelta(days=1)
            sc, pc = float(spot_daily.loc[day, "close"]), float(perp_daily.loc[day, "close"])
            if not (math.isfinite(sc) and math.isfinite(pc)):
                problems.append(f"no close for {day.date()}")
                break
            bars = perp_hourly[(perp_hourly.index >= day) & (perp_hourly.index < nxt)]
            if self.perp.position.qty < 0 and self.stop and bars.empty:
                problems.append(f"no perp 1h bars for {day.date()}: the stop cannot be checked yet")
                break
            p = self.pending
            fill_at = _ts(p["fill_at"]) if p else None
            if p and fill_at is not None and fill_at < nxt:
                if fill_at <= _ts(p["decided_at"]):
                    problems.append(f"simulator error: fill {fill_at.isoformat()} is not after {p['decided_at']}")
                    break
                ok = all(fill_at in h.index and math.isfinite(float(h.loc[fill_at, "open"])) for h in (spot_hourly, perp_hourly))
                if not ok:
                    problems.append(f"no 1h bar at {fill_at.isoformat()}: the order cannot fill yet")
                    break
                self._check_stop(bars[bars.index < fill_at], log)
                so, po = _px(spot_hourly.loc[fill_at, "open"]), _px(perp_hourly.loc[fill_at, "open"])
                self._fill(p, fill_at, so, po, log)
                if not self.bh_filled:
                    px = so * (1 + SLIP)
                    cash = self.bh.bal("cash")
                    q = (cash / (px * (1 + SPOT_FEE))).quantize(QTY_Q, ROUND_FLOOR)
                    fee = money(money(q * px) * SPOT_FEE)
                    self.bh.fill("fill:buy_and_hold", fill_at, "BUY", q, px, fee, reference_price=so)
                    self.trades.append({"date": str(day.date()), "at": fill_at.isoformat(), "book": "buy_and_hold",
                                        "leg": "spot", "side": "BUY", "qty": str(q), "price": str(px), "fee": str(fee)})
                    self.bh_filled = True
                self.pending = None
                self._check_stop(bars[bars.index >= fill_at], log)
            else:
                self._check_stop(bars, log)
            smark, pmark = _px(sc), _px(pc)
            close_at = nxt
            if day in funding.index and self.perp.position.qty != 0:
                est = bool(funding_estimated.get(day, False)) if funding_estimated is not None else False
                if self.perp.funding(f"perp:funding:{day.date()}", close_at, _px(funding.loc[day]), pmark):
                    self.perp.entries[-1].data["estimated"] = est
            if self.history:
                prev_tr, prev_ec = D(self.history[-1]["equity"]), D(self.history[-1]["economic_equity"])
            else:
                prev_tr = prev_ec = D(self.start_equity)
            self.risk.start_day(day, prev_tr, prev_ec)
            tr, ec = self.equity(smark, pmark), self.economic_equity(smark, pmark)
            n_alerts = len(self.risk.alerts)
            reason = self.risk.observe(close_at, tr, ec, self.profile)
            for a in self.risk.alerts[n_alerts:]:
                log({"kind": "alert", "date": str(day.date()), **a})
            if reason:
                halt = self._order(str(day.date()), 0.0, close_at, close_at,
                                   [f"HALT {reason}: close the short, sell all BTC and wait for a resume"], None, kind="halt")
                self.pending = halt
                log({**halt, "reason": reason, "trading_equity": str(tr), "high_water_mark": str(self.risk.high_water_mark)})
            sq, pq = self.spot.position.qty, self.perp.position.qty
            liq = self.liquidation_price()
            self.history.append({"date": str(day.date()), "close": sc, "perp_close": pc,
                                 "weight_held_at_close": self.weight, "spot_qty": str(sq), "perp_qty": str(pq),
                                 "spot_weight": str((sq * smark / tr).quantize(Decimal("1e-6"))) if tr > 0 else None,
                                 "perp_weight": str((pq * pmark / tr).quantize(Decimal("1e-6"))) if tr > 0 else None,
                                 "net_exposure": str(money(sq * smark + pq * pmark)), "equity": str(tr),
                                 "economic_equity": str(ec), "buy_and_hold": str(self.bh.trading_equity(smark)),
                                 "stop": None if not self.stop else self.stop["price"],
                                 "liquidation_price": None if liq is None else str(money(liq)),
                                 "halted": self.risk.halted})
            self.last_day = str(day.date())
            day = nxt
        later = spot_daily.index[spot_daily.index > day]
        if len(later) and day not in spot_daily.index:
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
        h = self.history[-1]
        return {"days": len(self.history), "since": self.history[0]["date"], "through": h["date"],
                "equity": float(eq.iloc[-1]), "buy_and_hold": float(bh.iloc[-1]),
                "return": float(eq.iloc[-1] / s0 - 1), "bh_return": float(bh.iloc[-1] / s0 - 1),
                "max_dd": dd(eq), "bh_max_dd": dd(bh), "weight_held": self.weight,
                "spot_weight": h["spot_weight"], "perp_weight": h["perp_weight"], "net_exposure": h["net_exposure"],
                "stop": h["stop"], "liquidation_price": h["liquidation_price"],
                "funding_received": str(-self.perp.bal("funding")),
                "fees": str(self.spot.bal("fees") + self.perp.bal("fees")),
                "trades": sum(1 for t in self.trades if t["book"] == "paper"),
                "stops": sum(1 for t in self.trades if t.get("reason") == "stop"),
                "alerts": len(self.risk.alerts), "halted": self.risk.halted, "halt_reason": self.risk.halt_reason,
                "ledger_problems": self.spot.check() + self.perp.check() + self.bh.check()
                + ([] if self.spot.bal("transfers") + self.perp.bal("transfers") == 0 else ["transfers do not net to zero"])}
