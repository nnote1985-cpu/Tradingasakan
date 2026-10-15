"""Append-only raw archive (BUILDPLAN v4.2 §5.2, §20.4).

Every file a collector receives is kept byte for byte under its sha256 and
recorded in ``raw_log.jsonl``, one hash-chained line per stored object. The
same bytes under the same ``source_key`` are stored once. Different bytes under
the same ``source_key`` (a late correction) become the next revision; earlier
revisions stay readable, so a snapshot taken before the correction can still be
replayed exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

GENESIS = "0" * 64


def _iso(t: datetime) -> str:
    if t.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return t.astimezone(UTC).isoformat()


def _line_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "line_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RawRecord:
    seq: int
    source_key: str
    revision: int
    sha256: str
    bytes: int
    received_at: datetime
    recorded_at: datetime
    checksum: str
    meta: dict

    @property
    def pointer(self) -> dict:
        return {"source_key": self.source_key, "revision": self.revision, "sha256": self.sha256}


def _record(d: dict) -> RawRecord:
    return RawRecord(seq=d["seq"], source_key=d["source_key"], revision=d["revision"], sha256=d["sha256"],
                     bytes=d["bytes"], received_at=datetime.fromisoformat(d["received_at"]),
                     recorded_at=datetime.fromisoformat(d["recorded_at"]), checksum=d["checksum"],
                     meta=d.get("meta", {}))


class RawStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.log_path = self.root / "raw_log.jsonl"
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)

    def _blob_path(self, sha: str) -> Path:
        return self.root / "blobs" / sha[:2] / sha

    def _lines(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(x) for x in self.log_path.read_text().splitlines() if x.strip()]

    def records(self, source_key: str | None = None, received_by: datetime | None = None) -> list[RawRecord]:
        out = [_record(d) for d in self._lines()]
        if source_key is not None:
            out = [r for r in out if r.source_key == source_key]
        if received_by is not None:
            out = [r for r in out if r.received_at <= received_by]
        return out

    def latest(self, source_key: str, received_by: datetime | None = None) -> RawRecord | None:
        recs = self.records(source_key, received_by)
        return max(recs, key=lambda r: r.revision) if recs else None

    def source_keys(self) -> list[str]:
        return sorted({d["source_key"] for d in self._lines()})

    def put(self, source_key: str, blob: bytes, received_at: datetime, checksum: str,
            meta: dict | None = None, recorded_at: datetime | None = None) -> tuple[str, RawRecord]:
        """Store ``blob``. Returns ("DUPLICATE" | "STORED" | "REVISION", record)."""
        sha = hashlib.sha256(blob).hexdigest()
        lines = self._lines()
        same = [d for d in lines if d["source_key"] == source_key]
        for d in same:
            if d["sha256"] == sha:
                return "DUPLICATE", _record(d)
        path = self._blob_path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)
        rec = {"seq": len(lines) + 1, "source_key": source_key, "revision": len(same) + 1, "sha256": sha,
               "bytes": len(blob), "received_at": _iso(received_at),
               "recorded_at": _iso(recorded_at or datetime.now(UTC)), "checksum": checksum,
               "meta": meta or {}, "prev_hash": lines[-1]["line_hash"] if lines else GENESIS}
        rec["line_hash"] = _line_hash(rec)
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return ("REVISION" if same else "STORED"), _record(rec)

    def read(self, rec: RawRecord) -> bytes:
        blob = self._blob_path(rec.sha256).read_bytes()
        if hashlib.sha256(blob).hexdigest() != rec.sha256:
            raise ValueError(f"blob {rec.sha256} does not match its hash")
        return blob

    def verify(self) -> list[str]:
        """Problems with the log chain or the blobs; empty when intact."""
        problems, prev = [], GENESIS
        for i, d in enumerate(self._lines(), start=1):
            if d.get("seq") != i:
                problems.append(f"line {i}: seq {d.get('seq')}")
            if d.get("prev_hash") != prev:
                problems.append(f"line {i}: chain broken")
            if _line_hash(d) != d.get("line_hash"):
                problems.append(f"line {i}: line hash does not match its content")
            p = self._blob_path(d["sha256"])
            if not p.exists():
                problems.append(f"line {i}: blob missing")
            elif hashlib.sha256(p.read_bytes()).hexdigest() != d["sha256"]:
                problems.append(f"line {i}: blob changed")
            prev = d.get("line_hash", "")
        return problems
