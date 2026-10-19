"""Run agent-v1 decisions dry: real models and live public data, no book and no orders (docs/agent-book-plan.md §4, step 2).

    uv run python scripts/agent_dry_run.py --decisions 3
    uv run python scripts/agent_dry_run.py --decisions 1 --position long

Each decision runs the three analysts, the Decider and, on BUY or SELL, the
Veto, exactly as the book will; the trigger is the 24 h ``time_check`` an
empty book gets. Everything (every model call, every tool call with its full
response, cost) goes to var/agent_dry_run/<UTC time>.jsonl, and spend is added
to var/agent_dry_run/ledger.jsonl, which holds the $1/day and $25/month caps
across runs. At the end it prints the cost per decision and how many decisions
a month the $25 cap allows.

Needs AGENT_ANTHROPIC_KEY (a key used for nothing else) and access to
api.binance.com and fapi.binance.com, so it runs on the worker's machine.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading.agent.market import http_get_json
from trading.agent.runner import AnthropicClient, Budget, Tools, Trigger, decide

KEY_ENV = "AGENT_ANTHROPIC_KEY"
DAILY_CAP = Decimal("1")
MONTHLY_CAP = Decimal("25")
OUT_DIR = Path("var/agent_dry_run")


def ledger_spend(path: Path, now: datetime) -> tuple[Decimal, Decimal]:
    """(spent today, spent this month), UTC, from earlier runs."""
    today = month = Decimal(0)
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            at = datetime.fromisoformat(row["at"])
            if (at.year, at.month) == (now.year, now.month):
                month += Decimal(row["cost_usd"])
                if at.date() == now.date():
                    today += Decimal(row["cost_usd"])
    return today, month


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--decisions", type=int, default=3, help="how many decisions to run (1-10)")
    ap.add_argument("--position", choices=["flat", "long"], default="flat")
    args = ap.parse_args(argv)
    if not 1 <= args.decisions <= 10:
        print("--decisions must be 1 to 10", file=sys.stderr)
        return 2
    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"{KEY_ENV} is not set (use a key that nothing else uses)", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clock = lambda: datetime.now(UTC)  # noqa: E731
    ledger = OUT_DIR / "ledger.jsonl"
    today, month = ledger_spend(ledger, clock())
    budget = Budget(DAILY_CAP, MONTHLY_CAP, today, month)
    print(f"spent before this run: ${today:.4f} today, ${month:.4f} this month (caps ${DAILY_CAP}/day, ${MONTHLY_CAP}/month)")
    client, tools = AnthropicClient(key), Tools(http_get_json, clock)
    out = OUT_DIR / f"{clock().strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    costs: list[Decimal] = []
    for n in range(args.decisions):
        now = clock()
        at_ms = int(now.timestamp() * 1000)
        trigger = Trigger(f"dry-{uuid.uuid4()}", "time_check", {"at_ms": str(at_ms)}, now.isoformat(),
                          "dry run: the 24 h time_check of a book with no active signal")
        d = decide("agent-v1/dry", args.position, trigger, [], client, tools, budget, clock)
        with out.open("a") as f:
            f.write(json.dumps(d.to_json(), default=str) + "\n")
        with ledger.open("a") as f:
            f.write(json.dumps({"at": clock().isoformat(), "decision_id": d.decision_id, "cost_usd": str(d.cost_usd)}) + "\n")
        costs.append(d.cost_usd)
        print(f"\ndecision {n + 1}: {d.action}{' (vetoed)' if d.vetoed else ''}, ${d.cost_usd:.4f}, "
              f"{len(d.new_signals)} new signals, {len(d.rejected_signals)} rejected")
        for r in d.runs:
            print(f"  {r.role:15} {r.model:18} {r.outcome:14} in {r.input_tokens:>7} out {r.output_tokens:>6} "
                  f"search {r.web_searches} tools {len(r.tool_calls):>2}  ${r.cost_usd:.4f}")
        if any(r.outcome == "BUDGET_BLOCKED" for r in d.runs):
            print("budget cap reached; stopping")
            break
    if costs:
        mid = Decimal(str(statistics.median(costs)))
        print(f"\ncost per decision: median ${mid:.4f}, max ${max(costs):.4f}; written to {out}")
        if mid > 0:
            print(f"$25/month allows about {int(MONTHLY_CAP / mid)} decisions a month ({MONTHLY_CAP / mid / 30:.1f} a day); "
                  f"$1/day allows about {int(DAILY_CAP / mid)} a day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
