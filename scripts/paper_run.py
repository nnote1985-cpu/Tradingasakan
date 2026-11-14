"""Run a frozen paper book and keep a forward record of when things happened.

    cd /home/user/frozen/<version>
    SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python /home/user/tradingasakan/scripts/paper_run.py

It runs the frozen worktree's own scripts/paper_trade.py unchanged (the book's rules
and code are the frozen ones); with --book pathA-v1.2 it runs scripts/paper_trade_v12.py
from the frozen/pathA-v1.2 worktree instead, and with --book pathA-p5-r1 the Phase 5 paired
run scripts/paper_trade_p5.py from frozen/pathA-p5-r1, with --book pathA-p5-r2 the r2 proposal run
scripts/paper_trade_p5r2.py from frozen/pathA-p5-r2, and with --book futures-r7 the R7 perp book
scripts/paper_trade_r7.py from frozen/futures-r7 (and pathA-r10a / futures-r11, the band books of
docs/band-books-plan.md, from their own frozen branches); it only observes them: every archive file fetched is
logged with the time it arrived and its sha256, then the run's output, the state
hash before and after, and the start and finish times. Each run is checked: a fill
at or before its decision time, or a decision stamped before its data arrived,
marks the run simulator_valid = false (the run is not valid evidence). One JSON line per run is
appended to runs.jsonl beside state.json. Each line carries the previous line's
hash, so an edited or removed line breaks the chain. The last line printed is SQL
that writes the line hash into trading.audit_events, whose server-side timestamp is
an independent record that the line existed at that time.

With --continuation <id> it runs the same frozen code as a continued version named
<book>-<id> (docs/data-patches.md): the state is the one scripts/continue_book.py copied into
paper-trading/<book>-<id>/, and the data patch named in its continued_from.json fills archive
bars that data.binance.vision never published. A patch row is used only where the archive has
no row; every patched file is logged with its archive sha256, the patched sha256 and the rows added.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ARCHIVE = "https://data.binance.vision/data"
PATCH_DIR = Path(__file__).resolve().parent.parent / "configs" / "data-patches"
ROOT = Path("/mnt/project-files/paper-trading")


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


def load_patch(patch_id: str, patch_dir: Path = PATCH_DIR) -> dict:
    """The patch file and the sha256 of its exact bytes."""
    raw = (patch_dir / f"{patch_id}.json").read_bytes()
    return {**json.loads(raw), "sha256": sha256(raw)}


def _zip_csv(name: str, text: str) -> bytes:
    """A deterministic zip (fixed timestamp), so the same rows always give the same sha256."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


def apply_patch(url: str, blob: bytes | None, patch: dict) -> tuple[bytes | None, dict | None]:
    """Add the patch's rows to an archive kline file that lacks them.

    Only the monthly file of a patched day's month and the patched day's daily file are touched,
    and only rows whose open_time the file does not already have are added: an archive row
    always wins, so the patch becomes a no-op if Binance ever publishes the day.
    """
    sym = patch["symbol"]
    for interval, rows in patch["rows"].items():
        stem = f"{ARCHIVE}/{patch['market']}/%s/{patch['kind']}/{sym}/{interval}/{sym}-{interval}-"
        for day in patch["days"]:
            if url == (stem % "monthly") + f"{day[:7]}.zip":
                want = [r for r in rows if datetime.fromtimestamp(int(r[0]) / 1000, UTC).date().isoformat()[:7] == day[:7]]
            elif url == (stem % "daily") + f"{day}.zip":
                want = [r for r in rows if datetime.fromtimestamp(int(r[0]) / 1000, UTC).date().isoformat() == day]
            else:
                continue
            if blob is not None:
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    name = z.namelist()[0]
                    text = z.read(name).decode()
            else:
                name, text = url.rsplit("/", 1)[1].replace(".zip", ".csv"), ",".join(patch["columns"]) + "\n"
            lines = [ln for ln in text.splitlines() if ln.strip()]
            header = [ln for ln in lines if not ln[0].isdigit()]
            data = [ln for ln in lines if ln[0].isdigit()]
            have = {ln.split(",", 1)[0] for ln in data}
            added = [r for r in want if r[0] not in have]
            info = {"patch": patch["id"], "patch_sha256": patch["sha256"],
                    "archive_sha256": None if blob is None else sha256(blob), "rows_added": len(added)}
            if not added:
                return blob, info
            data = sorted(data + [",".join(r) for r in added], key=lambda ln: int(ln.split(",", 1)[0]))
            return _zip_csv(name, "\n".join(header + data) + "\n"), info
    return blob, None


def main(argv: list[str] | None = None) -> int:
    import argparse

    from trading.alerts import live_data

    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=("pathA-v1.1", "pathA-v1.2", "pathA-p5-r1", "pathA-p5-r2", "futures-r7",
                                       "pathA-r10a", "futures-r11"), default="pathA-v1.1",
                    help="which frozen book to run; pathA-v1.1 is the default and runs exactly as before")
    ap.add_argument("--continuation", help="run the continued version <book>-<id> made by scripts/continue_book.py")
    ap.add_argument("--root", default=str(ROOT), help="folder holding the books")
    known, _ = ap.parse_known_args(argv)
    if known.book == "pathA-v1.2":
        from trading.shadow.rebalance_book import RULES_VERSION
        script = "scripts/paper_trade_v12.py"
    elif known.book == "pathA-p5-r1":
        from trading.shadow.paired_book import EXPERIMENT_ID as RULES_VERSION
        script = "scripts/paper_trade_p5.py"
    elif known.book == "pathA-p5-r2":
        from trading.shadow.proposal_book import EXPERIMENT_ID as RULES_VERSION
        script = "scripts/paper_trade_p5r2.py"
    elif known.book == "futures-r7":
        from trading.shadow.trend_perp_book import RULES_VERSION
        script = "scripts/paper_trade_r7.py"
    elif known.book == "pathA-r10a":
        from trading.shadow.band_books import R10A_RULES_VERSION as RULES_VERSION
        script = "scripts/paper_trade_r10a.py"
    elif known.book == "futures-r11":
        from trading.shadow.band_books import R11_RULES_VERSION as RULES_VERSION
        script = "scripts/paper_trade_r11.py"
    else:
        from trading.paper.ledger import RULES_VERSION
        script = "scripts/paper_trade.py"
    if RULES_VERSION != known.book:
        raise SystemExit(f"this worktree's code is {RULES_VERSION}, not {known.book}: run it from frozen/{known.book}")
    name = f"{RULES_VERSION}-{known.continuation}" if known.continuation else RULES_VERSION
    ap.add_argument("--state", default=str(Path(known.root) / name / "state.json"),
                    help="book state (a copy for a dry run); runs.jsonl is written beside it")
    args = ap.parse_args(argv)
    state = Path(args.state)
    log = state.with_name("runs.jsonl")
    patch, extra = None, {}
    if known.continuation:
        marker = state.with_name("continued_from.json")
        if not marker.exists():
            raise SystemExit(f"{marker} is missing: make {name} with scripts/continue_book.py first")
        cont = json.loads(marker.read_text())
        if cont.get("book") != name or cont.get("base_book") != RULES_VERSION:
            raise SystemExit(f"{marker} is for {cont.get('book')} from {cont.get('base_book')}, not {name}")
        patch = load_patch(cont["data_patch"]["id"])
        if patch["sha256"] != cont["data_patch"]["sha256"]:
            raise SystemExit(f"data patch {patch['id']} changed since {name} was made: a changed patch is a new version")
        extra = {"base_book": RULES_VERSION, "data_patch": {"id": patch["id"], "sha256": patch["sha256"]}}
    elif (state.parent / "superseded_by.json").exists():
        raise SystemExit(f"{name} was continued as {json.loads((state.parent / 'superseded_by.json').read_text())['book']}: "
                         "run that version instead")
    chain_problems = verify_chain(log)
    rec: dict = {"record_version": 1, "book": name, **extra, "started_at": now(),
                 "code_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                 "state_sha256_before": sha256(state.read_bytes()) if state.exists() else None, "fetched": []}

    original = live_data._get_verified

    def recording(url: str) -> bytes | None:
        blob = original(url)
        info = None
        if patch is not None:
            blob, info = apply_patch(url, blob, patch)
        if blob is not None:
            rec["fetched"].append({"url": url, "received_at": now(), "sha256": sha256(blob),
                                   **({"patched": info} if info else {})})
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
          f"('paper_run', '{name}', 'runs.jsonl line', "
          f"{('null' if rec['prev_hash'] is None else repr(rec['prev_hash']))}, '{rec['line_hash']}');")
    if rec.get("simulator_valid") is False:
        print("SIMULATOR INVALID for this run: " + json.dumps(rec["checks"], ensure_ascii=False), file=sys.stderr)
    return 0 if rec["exit"] == "ok" and rec.get("simulator_valid", True) else 1


if __name__ == "__main__":
    sys.exit(main())
