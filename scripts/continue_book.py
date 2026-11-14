"""Start a continued version of a paper book: same frozen code, its state carried over, plus a data patch.

    uv run python scripts/continue_book.py --book pathA-v1.2 --as g1 [--patch gap1]

Makes paper-trading/<book>-<as>/ with
  state.json           a byte-for-byte copy of the base book's state.json (never edited)
  journal.md           a copy of the base book's journal, when it has one
  continued_from.json  what was copied (sha256 of the state, the base runs.jsonl length and last
                       line hash, the base book's last code commit) and the data patch's id and sha256
and writes superseded_by.json beside the base book, so scripts/paper_run.py refuses to run it again.
The base book's files are otherwise left as they are. The continued book is then run with
scripts/paper_run.py --book <book> --continuation <as>. The last line printed is SQL that writes
the continuation into trading.audit_events (docs/data-patches.md).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.paper_run import ROOT, last_hash, load_patch, sha256, verify_chain
except ImportError:                       # run as a script: scripts/ is on sys.path
    from paper_run import ROOT, last_hash, load_patch, sha256, verify_chain


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, help="the base book, e.g. pathA-v1.2")
    ap.add_argument("--as", dest="suffix", required=True, help="continuation id; the new book is <book>-<id>")
    ap.add_argument("--patch", default="gap1", help="data patch id in configs/data-patches/")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)

    root = Path(args.root)
    base, new = root / args.book, root / f"{args.book}-{args.suffix}"
    state, log = base / "state.json", base / "runs.jsonl"
    if not state.exists() or not log.exists():
        raise SystemExit(f"{base} has no state.json and runs.jsonl")
    if (base / "superseded_by.json").exists():
        raise SystemExit(f"{args.book} was already continued")
    if new.exists():
        raise SystemExit(f"{new} already exists")
    problems = verify_chain(log)
    if problems:
        raise SystemExit(f"{log} chain is broken, not continuing: {problems}")
    patch = load_patch(args.patch)
    lines = log.read_text().strip().splitlines()
    last = json.loads(lines[-1])

    state_bytes = state.read_bytes()
    new.mkdir()
    (new / "state.json").write_bytes(state_bytes)
    if (base / "journal.md").exists():
        shutil.copyfile(base / "journal.md", new / "journal.md")
    marker = {
        "book": new.name,
        "base_book": args.book,
        "base_state_sha256": sha256(state_bytes),
        "base_runs_lines": len(lines),
        "base_runs_last_line_hash": last_hash(log),
        "base_last_code_commit": last.get("code_commit"),
        "data_patch": {"id": patch["id"], "sha256": patch["sha256"], "days": patch["days"]},
        "created_at": datetime.now(UTC).isoformat(),
        "note": "state.json is a byte copy of the base book's state at created_at; the base book is no longer run. "
                "Decisions the base book already recorded are kept as they are and never recomputed.",
    }
    raw = (json.dumps(marker, indent=1, ensure_ascii=False) + "\n").encode()
    (new / "continued_from.json").write_bytes(raw)
    (base / "superseded_by.json").write_text(json.dumps({"book": new.name, "at": marker["created_at"]}) + "\n")
    if sha256((new / "state.json").read_bytes()) != marker["base_state_sha256"]:
        raise SystemExit("copied state.json does not match the base state")
    print(json.dumps({"book": new.name, "base_state_sha256": marker["base_state_sha256"]}, ensure_ascii=False))
    print("insert into trading.audit_events (action, target, reason, before_hash, after_hash) values "
          f"('paper_continue', '{new.name}', 'continued from {args.book} with data patch {patch['id']}', "
          f"'{marker['base_runs_last_line_hash']}', '{sha256(raw)}');")
    return 0


if __name__ == "__main__":
    sys.exit(main())
