"""Run a frozen paper book and keep a forward record of when things happened.

    cd /home/user/frozen/<version>
    SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python /home/user/tradingasakan/scripts/paper_run.py

It runs the frozen worktree's own scripts/paper_trade.py unchanged (the book's rules
and code are the frozen ones); with --book pathA-v1.2 it runs scripts/paper_trade_v12.py
from the frozen/pathA-v1.2 worktree instead, and with --book pathA-p5-r1 the Phase 5 paired
run scripts/paper_trade_p5.py from frozen/pathA-p5-r1; it only observes them: every archive file fetched is
logged with the time it arrived and its sha256, then the run's output, the state
hash before and after, and the start and finish times. Each run is checked: a fill
at or before its decision time, or a decision stamped before its data arrived,
marks the run simulator_valid = false (the run is not valid evidence). One JSON line per run is
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


def main(argv: list[str] | None = None) -> int:
    import argparse

    from trading.alerts import live_data

    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=("pathA-v1.1", "pathA-v1.2", "pathA-p5-r1"), default="pathA-v1.1",
                    help="which frozen book to run; pathA-v1.1 is the default and runs exactly as before")
    known, _ = ap.parse_known_args(argv)
    if known.book == "pathA-v1.2":
        from trading.shadow.rebalance_book import RULES_VERSION
        script = "scripts/paper_trade_v12.py"
    elif known.book == "pathA-p5-r1":
        from trading.shadow.paired_book import EXPERIMENT_ID as RULES_VERSION
        script = "scripts/paper_trade_p5.py"
    else:
        from trading.paper.ledger import RULES_VERSION
        script = "scripts/paper_trade.py"
    if RULES_VERSION != known.book:
        raise SystemExit(f"this worktree's code is {RULES_VERSION}, not {known.book}: run it from frozen/{known.book}")
    ap.add_argument("--state", default=f"/mnt/project-files/paper-trading/{RULES_VERSION}/state.json",
                    help="book state (a copy for a dry run); runs.jsonl is written beside it")
    args = ap.parse_args(argv)
    state = Path(args.state)
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
    sys.argv = [Path(script).name, "--state", str(state)]
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(script, run_name="__main__")
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
        t = datetime.fromisoformat
        last_fetch = max((t(f["received_at"]) for f in rec["fetched"]), default=None)
        entries = result.get("new_entries", [])
        checks = {
            # a trade must fill strictly after the decision it executes was computed
            "fill_not_after_decision": [e["at"] for e in entries if e.get("kind") == "trade"
                                        and e.get("decided_at") and t(e["at"]) <= t(e["decided_at"])],
            # decision times must come after every file this run fetched
            "stamped_before_data_arrived": [e.get("decided_at", e.get("at")) for e in entries
                                            if e.get("kind") in ("open", "decision", "no_decision") and last_fetch
                                            and t(e.get("data_ready_at") or e["at"]) < last_fetch],
            "decided_before_data_ready": [e["decided_at"] for e in entries if e.get("data_ready_at")
                                          and e.get("decided_at") and t(e["decided_at"]) < t(e["data_ready_at"])],
            "simulator_errors": [p for p in result.get("problems", []) if "simulator error" in p],
        }
        rec["checks"] = checks
        rec["simulator_valid"] = not any(checks.values())
    rec["prev_hash"] = last_hash(log)
    rec["chain_problems_before_append"] = chain_problems
    rec["line_hash"] = sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode())
    with log.open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    print()
    print("insert into trading.audit_events (action, target, reason, before_hash, after_hash) values "
          f"('paper_run', '{RULES_VERSION}', 'runs.jsonl line', "
          f"{('null' if rec['prev_hash'] is None else repr(rec['prev_hash']))}, '{rec['line_hash']}');")
    if rec.get("simulator_valid") is False:
        print("SIMULATOR INVALID for this run: " + json.dumps(rec["checks"], ensure_ascii=False), file=sys.stderr)
    return 0 if rec["exit"] == "ok" and rec.get("simulator_valid", True) else 1


if __name__ == "__main__":
    sys.exit(main())
