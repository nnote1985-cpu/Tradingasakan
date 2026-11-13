"""Record one spot-e1-v1 daily run for the mirrors and the audit trail (docs/e1-paper-book-plan.md).

    uv run python scripts/e1_run_record.py --before   > /tmp/e1.before
    (cd /home/user/frozen/spot-e1-v1 && uv run python scripts/paper_trade_e1.py) > /tmp/e1.out
    uv run python scripts/e1_run_record.py --out /tmp/e1.out --before-file /tmp/e1.before

paper_trade_e1.py (frozen code) prints a JSON summary, a blank line, then Thai text, and keeps no
run log. This script, run from main around it:
  * saves the JSON summary as <book>/daily/<data_through>.json, which trading_sync_sql.py turns
    into equity snapshots (the state keeps trades, not daily marks); a run with no data_through
    (the opening run, or data not ready) saves nothing;
  * prints, as its last line, SQL that writes the run into trading.audit_events with the sha256 of
    state.json before and after the run.
It never writes state.json or journal.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BOOK = Path("/mnt/project-files/paper-trading/spot-e1-v1")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "none"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=str(BOOK))
    ap.add_argument("--before", action="store_true", help="print the state's sha256 and exit")
    ap.add_argument("--out", help="the run's stdout")
    ap.add_argument("--before-file", help="what --before printed")
    args = ap.parse_args(argv)
    book = Path(args.book)
    state = book / "state.json"
    if args.before:
        print(sha(state))
        return 0
    if not args.out or not args.before_file:
        ap.error("--out and --before-file are needed")
    text = Path(args.out).read_text()
    summary = json.loads(text.split("\n\n", 1)[0])
    before, after = Path(args.before_file).read_text().strip(), sha(state)
    saved = None
    if summary.get("data_through") and not summary.get("problems"):
        saved = book / "daily" / f"{summary['data_through']}.json"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"saved": str(saved) if saved else None, "state_changed": before != after,
                      "problems": summary.get("problems", []), "halted": summary.get("halted", False)}, ensure_ascii=False))
    reason = "opened" if before == "none" else ("state.json updated" if before != after else "no change")
    print("insert into trading.audit_events (action, target, reason, before_hash, after_hash) values "
          f"('paper_run', 'spot-e1-v1', '{reason}', '{before}', '{after}');")
    return 0


if __name__ == "__main__":
    sys.exit(main())
