"""Phase 5 on path A: the Code / AI paired run (docs/phase-5-paired-plan.md).

Both arms are ``PathABook``s (pathA-v1.2 rules on the spot journal with ``spot.v1``) opened on
the same close from the same cash. The Code arm takes the Code target every day. The AI arm
takes it too, except that a target **above** its current weight is a candidate the AI filter
must ALLOW; anything else (VETO, ABSTAIN, a failure) keeps its weight. Decreases and HALT sells
are never filtered. Each billed model call is an operating cost in the AI arm's journal, so it
lowers economic equity but not trading equity. A HALT is absorbing: there is no resume in this
run. The run ends at its planned endpoint; Random Veto paths are replayed from the recorded
Code targets (``random_veto``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from trading.shadow.rebalance_book import START_EQUITY, PathABook, _ts

EXPERIMENT_ID = "pathA-p5-r1"
BOOK_FORMAT = "paired_book.v1"
TIMEBOX_DAYS = 28
MONTHLY_CAP_USD = Decimal("5")
RV_SEEDS = 200
RV_PROBABILITIES = (0.25, 0.5, 0.75)

# ask(kind, proposal_id, current_weight, target_weight) -> record from trading.analysis.pathA_filter.ask
Asker = Callable[[str, str, float, float], dict[str, Any]]


def _target_of(book: PathABook) -> float:
    return float((book.pending or {}).get("weight", book.weight))


class PairedBook:
    def __init__(self, state: dict | None = None):
        if state is None:
            self.code, self.ai = PathABook(), PathABook()
            self.opened_on: str | None = None
            self.targets: list[dict] = []          # the Code target of every close (Random Veto replays these)
            self.filter_log: list[dict] = []       # every AI filter question and its decision
            self.diagnostics: list[dict] = []      # daily diagnostic verdicts: recorded, never acted on
            self.journal: list[dict] = []
        else:
            if state.get("format") != BOOK_FORMAT or state.get("experiment_id") != EXPERIMENT_ID:
                raise ValueError("state belongs to another format or experiment")
            self.code, self.ai = PathABook(state["code"]), PathABook(state["ai"])
            for k in ("opened_on", "targets", "filter_log", "diagnostics", "journal"):
                setattr(self, k, state[k])

    def to_dict(self) -> dict:
        return {"format": BOOK_FORMAT, "experiment_id": EXPERIMENT_ID, "opened_on": self.opened_on,
                "code": self.code.to_dict(), "ai": self.ai.to_dict(), "targets": self.targets,
                "filter_log": self.filter_log, "diagnostics": self.diagnostics, "journal": self.journal}

    # ---- calendar and budget -----------------------------------------------------------------
    @property
    def endpoint(self) -> str | None:
        """Last close of the run (inclusive): the opening close plus the timebox."""
        return None if self.opened_on is None else str(date.fromisoformat(self.opened_on) + timedelta(days=TIMEBOX_DAYS))

    @property
    def last_day(self) -> str | None:
        return self.code.last_day

    def spent_usd(self, month: str | None = None) -> Decimal:
        recs = self.filter_log + self.diagnostics
        return sum((Decimal(r["cost_usd"]) for r in recs if month is None or r["decided_at"][:7] == month), Decimal(0))

    def budget_left(self, month: str) -> Decimal:
        return MONTHLY_CAP_USD - self.spent_usd(month)

    # ---- decisions -----------------------------------------------------------------------------
    def _book_cost(self, rec: dict) -> None:
        c = Decimal(rec["cost_usd"])
        if c > 0:
            self.ai.book.operating_cost(f"llm:{rec['proposal_id']}", rec["attempt"]["response_at"], c,
                                        f"{rec['kind']} {rec['attempt']['model_id']}")

    def _ask_ai(self, decided_on: str, weight: float, data_ready_at, decided_at, notes: list[str],
                ask: Asker, opening: bool) -> dict | None:
        current = 0.0 if opening else _target_of(self.ai)
        rec = ask("filter", f"p5:{decided_on}:filter", current, weight)
        self.filter_log.append(rec)
        self._book_cost(rec)
        d = rec["decision"]["decision"]
        stamp = pd.Timestamp(rec["decided_at"])
        if opening:
            w = weight if d == "ALLOW" else 0.0
            return self.ai.open(decided_on, w, data_ready_at, max(stamp, _ts(decided_at)),
                                notes + [f"AI {d} {rec['decision']['reason_code']}"])
        if d == "ALLOW":
            return self.ai.decide(decided_on, weight, data_ready_at, max(stamp, _ts(decided_at)), notes + ["AI ALLOW"])
        e = {"kind": "filtered", "decided_on": decided_on, "weight": current, "code_weight": weight,
             "decision": d, "reason_code": rec["decision"]["reason_code"], "decided_at": rec["decided_at"],
             "data_ready_at": _ts(data_ready_at).isoformat()}
        self.ai.journal.append(e)
        return e

    def open(self, decided_on: str, weight: float, data_ready_at, decided_at, notes: list[str], ask: Asker) -> list[dict]:
        if self.opened_on is not None:
            raise ValueError("run already open")
        self.opened_on = decided_on
        self.targets.append({"decided_on": decided_on, "weight": weight, "data_ready_at": _ts(data_ready_at).isoformat(),
                             "decided_at": _ts(decided_at).isoformat()})
        out = [{"arm": "code", **self.code.open(decided_on, weight, data_ready_at, decided_at, notes, START_EQUITY)}]
        if weight > 0:
            e = self._ask_ai(decided_on, weight, data_ready_at, decided_at, notes, ask, opening=True)
        else:
            e = self.ai.open(decided_on, weight, data_ready_at, decided_at, notes, START_EQUITY)
        out.append({"arm": "ai", **(e or {})})
        self.journal += out
        return out

    def decide(self, decided_on: str, weight: float | None, data_ready_at, decided_at, notes: list[str],
               ask: Asker, diagnostic: bool = True) -> list[dict]:
        """The day's Code target for both arms, then the diagnostic question. Nothing after the endpoint."""
        if self.opened_on is None or self.endpoint is None or decided_on > self.endpoint or decided_on != self.code.last_day:
            return []
        if any(t["decided_on"] == decided_on for t in self.targets):
            return []                                                   # already decided on this close
        self.targets.append({"decided_on": decided_on, "weight": weight, "data_ready_at": _ts(data_ready_at).isoformat(),
                             "decided_at": _ts(decided_at).isoformat()})
        out: list[dict] = []
        if e := self.code.decide(decided_on, weight, data_ready_at, decided_at, notes):
            out.append({"arm": "code", **e})
        if decided_on == self.ai.last_day:
            if self.ai.risk.halted or weight is None or weight <= _target_of(self.ai):
                e = self.ai.decide(decided_on, weight, data_ready_at, decided_at, notes)
            else:
                e = self._ask_ai(decided_on, weight, data_ready_at, decided_at, notes, ask, opening=False)
            if e:
                out.append({"arm": "ai", **e})
            if diagnostic and weight is not None and not self.ai.risk.halted and decided_on < self.endpoint:
                rec = ask("diagnostic", f"p5:{decided_on}:diag", _target_of(self.ai), weight)
                self.diagnostics.append(rec)
                self._book_cost(rec)
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

    def performance(self) -> dict:
        c, a = self.code.performance(), self.ai.performance()
        h = self.ai.history[-1] if self.ai.history else None
        econ = float(h["economic_equity"]) if h else float(START_EQUITY)
        kinds = [r["decision"]["decision"] + ":" + r["decision"]["reason_code"] for r in self.filter_log]
        return {"experiment_id": EXPERIMENT_ID, "opened_on": self.opened_on, "endpoint": self.endpoint,
                "code": c, "ai": a | {"economic_equity": econ}, "cash": float(START_EQUITY),
                "filter_questions": len(self.filter_log), "filter_outcomes": {k: kinds.count(k) for k in sorted(set(kinds))},
                "diagnostic_questions": len(self.diagnostics), "spent_usd": str(self.spent_usd())}


# ---- Random Veto (§16.3) ---------------------------------------------------------------------

def uniform(seed: int, candidate_id: str) -> float:
    """Deterministic U[0,1) from seed and candidate id."""
    return int(hashlib.sha256(f"{seed}:{candidate_id}".encode()).hexdigest()[:16], 16) / 2.0**64


def random_veto(targets: list[dict], daily: pd.DataFrame, hourly: pd.DataFrame, p: float, seed: int,
                endpoint: str | None = None) -> PathABook:
    """Replay a full portfolio path in which each increase is vetoed with probability ``p``."""
    book = PathABook()
    if endpoint is not None:
        daily = daily[daily.index <= _ts(endpoint)]
    for i, t in enumerate(targets):
        w = t["weight"]
        if i == 0:
            veto = w is not None and w > 0 and uniform(seed, f"{t['decided_on']}:filter") < p
            book.open(t["decided_on"], 0.0 if veto else w, t["data_ready_at"], t["decided_at"], ["rv"])
            continue
        day = _ts(t["decided_on"])
        book.advance(daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)])
        if w is not None and not book.risk.halted and w > _target_of(book) and uniform(seed, f"{t['decided_on']}:filter") < p:
            continue
        book.decide(t["decided_on"], w, t["data_ready_at"], t["decided_at"], ["rv"])
    book.advance(daily, hourly)
    return book


def random_veto_summary(targets: list[dict], daily: pd.DataFrame, hourly: pd.DataFrame,
                        endpoint: str | None = None, seeds: int = RV_SEEDS) -> dict[str, dict[str, float]]:
    out = {}
    for p in RV_PROBABILITIES:
        eqs = sorted(random_veto(targets, daily, hourly, p, s, endpoint).performance()["equity"] for s in range(seeds))
        out[str(p)] = {"seeds": seeds, "p05": eqs[int(0.05 * (seeds - 1))], "median": eqs[(seeds - 1) // 2],
                       "p95": eqs[int(0.95 * (seeds - 1))]}
    return out
