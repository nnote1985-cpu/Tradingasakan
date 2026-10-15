"""Archive budget (BUILDPLAN v4.2 §4.4) measured from a raw store.

    python scripts/data_budget.py --store DIR

Reports stored bytes per dataset per collected day and projects 30 days and a
year (one revision per file; corrections add to this). Tick, L1 and trade feeds
are not collected from this environment (docs/phase-2-data.md) and are listed
as not measured rather than estimated.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from trading.data.raw_store import RawStore


def budget(store: RawStore) -> dict:
    per: dict = defaultdict(lambda: {"files": 0, "bytes": 0, "days": set(), "revisions": 0})
    for r in store.records():
        d = per[r.meta.get("dataset", "unknown")]
        d["files"] += 1
        d["bytes"] += r.bytes
        d["revisions"] += r.revision > 1
        d["days"].add(r.meta.get("day"))
    out, total_day = {}, 0.0
    for name, d in sorted(per.items()):
        monthly = name == "funding"
        span_days = 30 * len(d["days"]) if monthly else len(d["days"])
        per_day = d["bytes"] / span_days if span_days else 0.0
        total_day += per_day
        out[name] = {"files": d["files"], "bytes": d["bytes"], "revisions": d["revisions"],
                     "bytes_per_day": round(per_day), "projected_30d_bytes": round(per_day * 30),
                     "projected_365d_bytes": round(per_day * 365)}
    blobs = sum(p.stat().st_size for p in (store.root / "blobs").rglob("*") if p.is_file())
    log = store.log_path.stat().st_size if store.log_path.exists() else 0
    return {"datasets": out, "total_bytes_per_day": round(total_day), "projected_365d_bytes": round(total_day * 365),
            "on_disk": {"blobs": blobs, "raw_log": log},
            "not_measured": ["tick trades", "L1 quotes", "depth", "mark price stream",
                             "(REST and websockets unreachable from this runtime; archive bars only)"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, type=Path)
    print(json.dumps(budget(RawStore(ap.parse_args().store)), indent=2))
