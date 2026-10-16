"""Local emergency journal: append-only JSON lines with fsync and a hash chain (§14.1).

Guardian writes here first when the store cannot be written (disk full, locked
file), so an emergency action is never lost. Each line carries the hash of the
previous line, so a truncated or edited journal is detected on read. ``sync``
hands each unsynced line to a callback once and remembers how far it got.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class JournalCorrupt(RuntimeError):
    pass


class EmergencyJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.cursor_path = path.with_suffix(path.suffix + ".synced")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        prev = GENESIS
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
            rec = json.loads(line)
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != n or rec.get("prev") != prev or rec.get("hash") != _hash(body):
                raise JournalCorrupt(f"line {n} does not chain")
            prev = rec["hash"]
            out.append(rec)
        return out

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        recs = self.read()
        body = {"seq": len(recs), "prev": recs[-1]["hash"] if recs else GENESIS, "kind": kind, "data": data}
        rec = {**body, "hash": _hash(body)}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec

    def synced_upto(self) -> int:
        return int(self.cursor_path.read_text()) if self.cursor_path.exists() else 0

    def sync(self, apply: Callable[[dict[str, Any]], None]) -> int:
        """Apply unsynced records in order; the cursor moves only after each one succeeds."""
        recs = self.read()
        done = self.synced_upto()
        for rec in recs[done:]:
            apply(rec)
            done += 1
            self.cursor_path.write_text(str(done))
        return done


def _hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
