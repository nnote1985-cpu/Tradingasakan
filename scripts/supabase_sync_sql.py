"""Print idempotent SQL that mirrors paper books (and optionally a risk check) into Supabase.

    uv run python scripts/supabase_sync_sql.py [--risk-json PATH] > sync.sql

Reads every /mnt/project-files/paper-trading/<version>/state.json in the pathA-v1
float format; pathA-v1.2 books (format rebalance_book.v1) are skipped here and
mirrored only by scripts/trading_sync_sql.py. The output
only inserts or updates rows keyed by version/day/time, so running it twice
changes nothing. Run the SQL through the Supabase SQL editor or the Supabase
connector; no credentials are stored here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/mnt/project-files/paper-trading")


def q(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(float(v))
    return "'" + str(v).replace("'", "''") + "'"


def j(v) -> str:
    return q(json.dumps(v, ensure_ascii=False)) + "::jsonb"


def book_sql(state: dict) -> list[str]:
    v = state["rules_version"]
    opened = state["journal"][0]["date"]
    out = [f"insert into paper.books (rules_version, frozen_branch, start_equity, opened_on) values "
           f"({q(v)}, {q('frozen/' + v)}, {q(state['start_equity'])}, {q(opened)}) "
           f"on conflict (rules_version) do update set updated_at = now();"]
    for i, e in enumerate(state["journal"]):
        out.append("insert into paper.journal (rules_version, seq, close_date, kind, weight, at, notes) values "
                   f"({q(v)}, {i}, {q(e['date'])}, {q(e['kind'])}, {q(e['weight'])}, {q(e.get('at'))}, {j(e['notes'])}) "
                   "on conflict (rules_version, seq) do nothing;")
    for t in state["trades"]:
        out.append("insert into paper.trades (rules_version, book, filled_at, side, price, qty, value) values "
                   f"({q(v)}, {q(t['book'])}, {q(t['at'])}, {q(t['side'])}, {q(t['price'])}, {q(t['qty'])}, {q(t['value'])}) "
                   "on conflict do nothing;")
    for h in state["history"]:
        out.append("insert into paper.daily_equity (rules_version, day, close, weight_held_at_close, equity, buy_and_hold) "
                   f"values ({q(v)}, {q(h['date'])}, {q(h['close'])}, {q(h['weight_held_at_close'])}, "
                   f"{q(h['equity'])}, {q(h['buy_and_hold'])}) on conflict do nothing;")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--risk-json")
    args = ap.parse_args()
    lines = ["begin;"]
    for p in sorted(Path(args.root).glob("*/state.json")):
        state = json.loads(p.read_text())
        if state.get("format"):     # pathA-v1.2 and later: mirrored to the trading schema only
            print(f"-- {state.get('rules_version') or state.get('experiment_id')} ({state['format']}) is not mirrored to paper.*", file=sys.stderr)
            continue
        lines += book_sql(state)
    if args.risk_json:
        s = json.loads(Path(args.risk_json).read_text())
        lines.append(f"insert into paper.risk_checks (day, summary) values ({q(s['date'])}, {j(s)}) "
                     "on conflict (day) do update set summary = excluded.summary, run_at = now();")
    lines.append("commit;")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
