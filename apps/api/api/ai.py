"""AI tab (BUILDPLAN v4.2 §21.3 features/analysis, §16.4): the locked filter settings and a
diagnostic scorecard of what the AI's answers would have meant.

Settings are read-only: the Phase 5 run is locked, and any change is a new revision (§7.1).
The scorecard compares each answer with the BTC close ``horizon`` days later, using the AI
arm's own daily marks. It is diagnostic only (§16.4): it never feeds back into a portfolio.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from trading.analysis import pathA_filter as pf
from trading.shadow import paired_book as pb

RATIO = Decimal("0.000001")
HORIZON_DAYS = 7


def ai_settings() -> dict[str, Any]:
    g = pf.load_filter_graph()
    role = g.role("filter")
    return {
        "experiment_id": pf.EXPERIMENT_ID, "graph_version": g.graph_version, "locked": True,
        "capability_stage": g.capability_stage.value, "model_id": role.model_id, "effort": pf.EFFORT,
        "prompt_version": pf.PROMPT_VERSION, "prompt_hash": pf.prompt_hash(), "policy_version": pf.FILTER_POLICY,
        "context_profile": "quant_only", "tools": list(g.tools), "timeout_ms": role.timeout_ms,
        "max_attempts": g.max_attempts_per_role, "max_run_cost_usd": g.max_run_cost_usd,
        "daily_cost_cap_usd": g.daily_cost_cap_usd, "monthly_cap_usd": str(pb.MONTHLY_CAP_USD),
        "timebox_days": pb.TIMEBOX_DAYS, "filters": "increases only; decreases and HALT sells always pass",
        "on_failure": "no increase (fail closed)",
    }


def scorecard(decisions: list[dict[str, Any]], marks: list[tuple[Any, ...]], horizon: int = HORIZON_DAYS) -> dict[str, Any]:
    """``decisions`` are decision_items()["items"]; ``marks`` are equity_rows (at, ..., mark_price, ...).

    A no-increase answer (VETO, ABSTAIN or a failure) "avoided a loss" when BTC fell over the
    horizon and "missed a gain" when it rose; an ALLOW is "right" when BTC rose. Until the close
    ``horizon`` days after the decision exists, the answer is pending.
    """
    close = {at.date(): Decimal(mark) for at, _t, _e, mark, *_ in marks if mark is not None}
    items: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {"filter": {}, "diagnostic": {}}
    for d in decisions:
        day = date.fromisoformat(d["decided_on"])
        start, end = close.get(day), close.get(day + timedelta(days=horizon))
        held_back = d["outcome"] != "ALLOW"
        if start is None or end is None:
            verdict, ret = "pending", None
        else:
            r = end / start - 1
            ret = str(r.quantize(RATIO))
            if r == 0:
                verdict = "flat"
            elif held_back:
                verdict = "avoided_loss" if r < 0 else "missed_gain"
            else:
                verdict = "allowed_gain" if r > 0 else "allowed_loss"
        counts.setdefault(d["kind"], {})
        counts[d["kind"]][verdict] = counts[d["kind"]].get(verdict, 0) + 1
        items.append({"proposal_id": d["proposal_id"], "kind": d["kind"], "decided_on": d["decided_on"],
                      "outcome": d["outcome"], "forward_return": ret, "verdict": verdict})
    return {"horizon_days": horizon, "items": items, "counts": counts, "diagnostic_only": True}
