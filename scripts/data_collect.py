"""Collect archive files into the raw store, then take and replay a snapshot.

    python scripts/data_collect.py --store DIR --start 2026-09-01 --end 2026-10-14 [--snapshot]

Re-running is safe: files already stored are DUPLICATE, changed files become a
new revision. Exit code 1 when a snapshot fails to replay or the store fails
verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from trading.data.collector import Collector
from trading.data.raw_store import RawStore
from trading.data.snapshot import SnapshotLog, build_snapshot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", required=True, type=date.fromisoformat)
    ap.add_argument("--snapshot", action="store_true", help="take a snapshot as of now and replay every logged snapshot")
    a = ap.parse_args()

    store = RawStore(a.store)
    results = Collector(store).collect(a.symbol, a.start, a.end)
    counts = Counter((r["dataset"], r["status"]) for r in results)
    out: dict = {"collected": {f"{d}:{s}": n for (d, s), n in sorted(counts.items())},
                 "revisions": [r for r in results if r["status"] == "REVISION"]}
    problems = store.verify()
    out["store_problems"] = problems
    if a.snapshot:
        log = SnapshotLog(a.store / "snapshots.jsonl")
        now = datetime.now(UTC)
        snap = build_snapshot(store, a.symbol, now)
        log.append(snap, taken_at=now)
        out["snapshot"] = {"as_of": snap["as_of"], "sha256": snap["sha256"], "complete": snap["quality"]["complete"],
                           "problems": snap["quality"]["problems"], "funding": {k: snap["funding"][k] for k in
                           ("policy", "last_actual_print", "mean_7d_per_day", "proxy_days", "hot")}}
        out["replay"] = log.replay_all(store)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    bad_replay = any(not r["ok"] for r in out.get("replay", []))
    return 1 if problems or bad_replay else 0


if __name__ == "__main__":
    sys.exit(main())
