"""Run a frozen paper book and keep a forward record of when things happened.

    cd /home/user/frozen/<version>
    SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python /home/user/tradingasakan/scripts/paper_run.py

It runs the frozen worktree's own scripts/paper_trade.py unchanged (the book's rules
and code are the frozen ones) and only observes it: every archive file fetched is
logged with the time it arrived and its sha256, then the run's output, the state
hash before and after, and the start and finish times. One JSON line per run is
appended to runs.jsonl beside state.json. Each line carries the previous line's
hash, so an edited or removed line breaks the chain. The last line printed is SQL
that writes the line hash into trading.audit_events, whose server-side timestamp is
an independent record that the line existed at that time.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def last_hash(log: Path) -> str | None:
    if not log.exists():
        return None
    lines = log.read_text().strip().splitlines()
    return json.loads(lines[-1])["line_hash"] if lines else None


def verify_chain(log: Path) -> list[str]:
    """Problems in an existing log; empty when every line links to the one before."""
    problems, prev = [], None
    for i, line in enumerate(log.read_text().strip().splitlines() if log.exists() else [], 1):
        rec = json.loads(line)
        body = {k: v for k, v in rec.items() if k != "line_hash"}
        if rec.get("prev_hash") != prev:
            problems.append(f"line {i}: prev_hash does not match line {i - 1}")
        if sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()) != rec.get("line_hash"):
            problems.append(f"line {i}: line_hash does not match its content")
        prev = rec.get("line_hash")
    return problems


def main() -> int:
    from trading.alerts import live_data
    from trading.paper.ledger import RULES_VERSION

    state = Path(f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json")
    log = state.with_name("runs.jsonl")
    chain_problems = verify_chain(log)
    rec: dict = {"record_version": 1, "book": RULES_VERSION, "started_at": now(),
                 "code_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                 "state_sha256_before": sha256(state.read_bytes()) if state.exists() else None, "fetched": []}

    original = live_data._get_verified

    def recording(url: str) -> bytes | None:
        blob = original(url)
        if blob is not None:
            rec["fetched"].append({"url": url, "received_at": now(), "sha256": sha256(blob)})
        return blob

    live_data._get_verified = recording  # type: ignore[assignment]
    out = io.StringIO()
    sys.argv = ["paper_trade.py"]
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path("scripts/paper_trade.py", run_name="__main__")
        rec["exit"] = "ok"
    except SystemExit as e:
        rec["exit"] = f"exit {e.code}"
    finally:
        live_data._get_verified = original  # type: ignore[assignment]
    text = out.getvalue()
    sys.stdout.write(text)
    try:
        result, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except ValueError:
        result = None
    rec["finished_at"] = now()
    rec["result"] = result
    rec["state_sha256_after"] = sha256(state.read_bytes()) if state.exists() else None
    if result:
        last_fetch = max((f["received_at"] for f in rec["fetched"]), default=None)
        rec["checks"] = {
            # a decision stamped before its data had all arrived would allow a fill earlier than possible
            "decision_stamped_before_last_fetch": [e["at"] for e in result.get("new_entries", [])
                                                   if e.get("kind") in ("open", "decision") and last_fetch
                                                   and e["at"] < last_fetch],
        }
    rec["prev_hash"] = last_hash(log)
    rec["chain_problems_before_append"] = chain_problems
    rec["line_hash"] = sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode())
    with log.open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    print()
    print("insert into trading.audit_events (action, target, reason, before_hash, after_hash) values "
          f"('paper_run', '{RULES_VERSION}', 'runs.jsonl line', "
          f"{('null' if rec['prev_hash'] is None else repr(rec['prev_hash']))}, '{rec['line_hash']}');")
    return 0 if rec["exit"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
