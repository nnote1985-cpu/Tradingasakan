"""Phase 5 r2 on path A: the AI proposes the daily weight (docs/phase-5-r2-plan.md).

Two ``PathABook``s (pathA-v1.2 fills on the spot journal with ``spot.v1``) open on the same close
from the same cash. The Code arm takes the Code target every day. The AI arm takes the weight the
AI proposed once code has compiled it (``trading.analysis.pathA_proposer``); a failed proposal
keeps the AI arm's current weight. HALT sells to cash and is absorbing until the endpoint:
proposals after it are recorded and ignored. Each billed call is an operating cost in the AI arm,
so it lowers economic equity but not trading equity. Buy & hold is each book's own ``bh`` journal
and cash stays at the opening equity. The random proposer replays full paths with random weights
changing on as many days as the AI changed its target (``random_proposer``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from trading.contracts.analysis import SPOT_WEIGHTS
from trading.shadow.rebalance_book import START_EQUITY, PathABook, _ts

EXPERIMENT_ID = "pathA-p5-r2"
BOOK_FORMAT = "proposal_book.v1"
TIMEBOX_DAYS = 28
MONTHLY_CAP_USD = Decimal("5")
RP_SEEDS = 200

# propose(proposal_id, current_weight, code_weight) -> record from trading.analysis.pathA_proposer.propose
Proposer = Callable[[str, float, float | None], dict[str, Any]]


def _target_of(book: PathABook) -> float:
    return float((book.pending or {}).get("weight", book.weight))


class ProposalBook:
    def __init__(self, state: dict | None = None):
        if state is None:
            self.code, self.ai = PathABook(), PathABook()
            self.opened_on: str | None = None
            self.targets: list[dict] = []          # the Code target of every close
            self.proposals: list[dict] = []        # every AI proposal, compiled or rejected
            self.journal: list[dict] = []
        else:
            if state.get("format") != BOOK_FORMAT or state.get("experiment_id") != EXPERIMENT_ID:
                raise ValueError("state belongs to another format or experiment")
            self.code, self.ai = PathABook(state["code"]), PathABook(state["ai"])
            for k in ("opened_on", "targets", "proposals", "journal"):
                setattr(self, k, state[k])

    def to_dict(self) -> dict:
        return {"format": BOOK_FORMAT, "experiment_id": EXPERIMENT_ID, "opened_on": self.opened_on,
                "code": self.code.to_dict(), "ai": self.ai.to_dict(), "targets": self.targets,
                "proposals": self.proposals, "journal": self.journal}

    # ---- calendar and budget -----------------------------------------------------------------
    @property
    def endpoint(self) -> str | None:
        """Last close of the run (inclusive): the opening close plus the timebox."""
        return None if self.opened_on is None else str(date.fromisoformat(self.opened_on) + timedelta(days=TIMEBOX_DAYS))

    @property
    def last_day(self) -> str | None:
        return self.code.last_day

    def spent_usd(self, month: str | None = None) -> Decimal:
        return sum((Decimal(r["cost_usd"]) for r in self.proposals if month is None or r["decided_at"][:7] == month),
                   Decimal(0))

    def budget_left(self, month: str) -> Decimal:
        return MONTHLY_CAP_USD - self.spent_usd(month)

    # ---- decisions -----------------------------------------------------------------------------
    def _ask(self, decided_on: str, current: float, code_weight: float | None, propose: Proposer) -> dict:
        rec = propose(f"p5r2:{decided_on}:proposal", current, code_weight)
        self.proposals.append(rec)
        c = Decimal(rec["cost_usd"])
        if c > 0:
            self.ai.book.operating_cost(f"llm:{rec['proposal_id']}", rec["attempt"]["response_at"], c,
                                        f"proposal {rec['attempt']['model_id']}")
        return rec

    @staticmethod
    def _note(rec: dict) -> str:
        o = rec["outcome"]
        return f"AI {o['target_weight']:.0%} {o['reason_code']}" if o["accepted"] else f"AI ล้มเหลว {o['error_category']} คงสัดส่วนเดิม"

    def open(self, decided_on: str, code_weight: float, data_ready_at, decided_at, notes: list[str],
             propose: Proposer) -> list[dict]:
        if self.opened_on is not None:
            raise ValueError("run already open")
        self.opened_on = decided_on
        self.targets.append({"decided_on": decided_on, "weight": code_weight, "data_ready_at": _ts(data_ready_at).isoformat(),
                             "decided_at": _ts(decided_at).isoformat()})
        out = [{"arm": "code", **self.code.open(decided_on, code_weight, data_ready_at, decided_at, notes, START_EQUITY)}]
        rec = self._ask(decided_on, 0.0, code_weight, propose)       # before opening the AI arm holds cash
        e = self.ai.open(decided_on, rec["outcome"]["target_weight"], data_ready_at,
                         max(pd.Timestamp(rec["decided_at"]), _ts(decided_at)), notes + [self._note(rec)], START_EQUITY)
        out.append({"arm": "ai", **e})
        self.journal += out
        return out

    def decide(self, decided_on: str, code_weight: float | None, data_ready_at, decided_at, notes: list[str],
               propose: Proposer) -> list[dict]:
        """The day's Code target and AI proposal. Nothing after the endpoint, never twice on one close."""
        if self.opened_on is None or self.endpoint is None or decided_on > self.endpoint or decided_on != self.code.last_day:
            return []
        if any(t["decided_on"] == decided_on for t in self.targets):
            return []
        self.targets.append({"decided_on": decided_on, "weight": code_weight, "data_ready_at": _ts(data_ready_at).isoformat(),
                             "decided_at": _ts(decided_at).isoformat()})
        out: list[dict] = []
        if e := self.code.decide(decided_on, code_weight, data_ready_at, decided_at, notes):
            out.append({"arm": "code", **e})
        if decided_on == self.ai.last_day:
            if code_weight is None:                                   # inputs missing: no snapshot to ask about
                e = self.ai.decide(decided_on, None, data_ready_at, decided_at, notes)
            else:
                current = _target_of(self.ai)
                halted = self.ai.risk.halted
                rec = self._ask(decided_on, current, code_weight, propose)
                rec["ignored_halted"] = halted                         # recorded, never acted on
                stamp = max(pd.Timestamp(rec["decided_at"]), _ts(decided_at))
                e = self.ai.decide(decided_on, rec["outcome"]["target_weight"], data_ready_at, stamp,
                                   notes + [self._note(rec)])
                if e is None and not rec["outcome"]["accepted"]:
                    e = {"kind": "proposal_failed", "decided_on": decided_on, "weight": current,
                         "error_category": rec["outcome"]["error_category"], "decided_at": rec["decided_at"],
                         "data_ready_at": _ts(data_ready_at).isoformat()}
                    self.ai.journal.append(e)
            if e:
                out.append({"arm": "ai", **e})
        self.journal += out
        return out

    # ---- marks -----------------------------------------------------------------------------
    def advance(self, daily: pd.DataFrame, hourly: pd.DataFrame) -> tuple[list[dict], list[str]]:
        """Fill and mark both arms through the endpoint close; never past it."""
        if self.endpoint is not None:
            daily = daily[daily.index <= _ts(self.endpoint)]
        out, problems = [], []
        for arm, book in (("code", self.code), ("ai", self.ai)):
            added, p = book.advance(daily, hourly)
            out += [{"arm": arm, **e} for e in added]
            problems += [f"{arm}: {x}" for x in p]
        self.journal += out
        return out, problems

    # ---- report ------------------------------------------------------------------------------
    def ai_changes(self) -> int:
        """Closes on which the AI arm's compiled target differed from its previous one (cash before opening)."""
        prev, n = 0.0, 0
        for r in self.proposals:
            if r.get("ignored_halted"):
                continue
            w = r["outcome"]["target_weight"]
            n += w != prev
            prev = w
        return n

    def performance(self) -> dict:
        c, a = self.code.performance(), self.ai.performance()
        h = self.ai.history[-1] if self.ai.history else None
        econ = float(h["economic_equity"]) if h else float(START_EQUITY)
        accepted = [r for r in self.proposals if r["outcome"]["accepted"]]
        fails = [r["outcome"]["error_category"] for r in self.proposals if not r["outcome"]["accepted"]]
        lat = sorted((pd.Timestamp(r["attempt"]["response_at"]) - pd.Timestamp(r["attempt"]["request_at"])).total_seconds()
                     for r in self.proposals if r["attempt"] and r["attempt"]["response_at"])
        return {"experiment_id": EXPERIMENT_ID, "opened_on": self.opened_on, "endpoint": self.endpoint,
                "code": c, "ai": a | {"economic_equity": econ}, "cash": float(START_EQUITY),
                "proposals": len(self.proposals), "accepted": len(accepted),
                "agree_with_code": sum(r["outcome"]["target_weight"] == r["code_weight"] for r in accepted),
                "ai_changes": self.ai_changes(), "failures": {k: fails.count(k) for k in sorted(set(fails))},
                "latency_s": {"p50": lat[(len(lat) - 1) // 2], "p95": lat[int(0.95 * (len(lat) - 1))]} if lat else None,
                "spent_usd": str(self.spent_usd())}


# ---- random proposer (the turnover control) ------------------------------------------------

def uniform(seed: int, key: str) -> float:
    """Deterministic U[0,1) from seed and key."""
    return int(hashlib.sha256(f"r2:{seed}:{key}".encode()).hexdigest()[:16], 16) / 2.0**64


def random_weights(days: list[str], changes: int, seed: int) -> list[float]:
    """Random targets in {0, 0.5, 1} that differ from the previous one (cash before the first) on exactly
    ``min(changes, len(days))`` days, chosen deterministically from seed and day."""
    picked = set(sorted(days, key=lambda d: uniform(seed, f"{d}:when"))[:changes])
    out, prev = [], 0.0
    for d in days:
        if d in picked:
            choices = [w for w in SPOT_WEIGHTS if w != prev]
            prev = choices[int(uniform(seed, f"{d}:what") * len(choices))]
        out.append(prev)
    return out


def random_proposer(targets: list[dict], changes: int, daily: pd.DataFrame, hourly: pd.DataFrame, seed: int,
                    endpoint: str | None = None) -> PathABook:
    """Replay a full portfolio path on the recorded decision times with random weights."""
    days = [t["decided_on"] for t in targets if t["weight"] is not None]
    drawn = dict(zip(days, random_weights(days, changes, seed), strict=True))
    weights = [drawn.get(t["decided_on"]) for t in targets]
    book = PathABook()
    if endpoint is not None:
        daily = daily[daily.index <= _ts(endpoint)]
    for i, (t, w) in enumerate(zip(targets, weights, strict=True)):
        if i == 0:
            assert w is not None
            book.open(t["decided_on"], w, t["data_ready_at"], t["decided_at"], ["rp"])
            continue
        day = _ts(t["decided_on"])
        book.advance(daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)])
        book.decide(t["decided_on"], w, t["data_ready_at"], t["decided_at"], ["rp"])
    book.advance(daily, hourly)
    return book


def random_proposer_summary(book: ProposalBook, daily: pd.DataFrame, hourly: pd.DataFrame,
                            seeds: int = RP_SEEDS) -> dict[str, float]:
    eqs = sorted(random_proposer(book.targets, book.ai_changes(), daily, hourly, s, book.endpoint).performance()["equity"]
                 for s in range(seeds))
    ai = book.ai.performance()["equity"]
    return {"seeds": seeds, "changes": book.ai_changes(), "p05": eqs[int(0.05 * (seeds - 1))],
            "median": eqs[(seeds - 1) // 2], "p95": eqs[int(0.95 * (seeds - 1))],
            "ai_trading_equity_rank": sum(e < ai for e in eqs) / seeds}
