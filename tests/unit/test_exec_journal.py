"""Phase 4 deliverable 2: local emergency journal."""

from __future__ import annotations

import json

import pytest

from trading.execution.journal import EmergencyJournal, JournalCorrupt


def test_append_read_and_sync_once(tmp_path):
    j = EmergencyJournal(tmp_path / "emergency.jsonl")
    j.append("HALT", {"reason": "db down"})
    j.append("REDUCE", {"qty": "0.01"})
    seen: list[str] = []
    assert j.sync(lambda r: seen.append(r["kind"])) == 2
    assert j.sync(lambda r: seen.append(r["kind"])) == 2
    assert seen == ["HALT", "REDUCE"]                               # each record applied once


def test_failed_sync_resumes_where_it_stopped(tmp_path):
    j = EmergencyJournal(tmp_path / "e.jsonl")
    for k in ("A", "B", "C"):
        j.append(k, {})
    calls: list[str] = []

    def flaky(r):
        if r["kind"] == "B" and "B" not in calls:
            calls.append("B")
            raise ConnectionError
        calls.append(r["kind"])

    with pytest.raises(ConnectionError):
        j.sync(flaky)
    assert j.synced_upto() == 1
    j.sync(flaky)
    assert calls == ["A", "B", "B", "C"]


def test_edited_journal_is_detected(tmp_path):
    p = tmp_path / "e.jsonl"
    j = EmergencyJournal(p)
    j.append("HALT", {"reason": "x"})
    j.append("REDUCE", {"qty": "1"})
    lines = p.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["data"]["qty"] = "2"
    p.write_text(lines[0] + "\n" + json.dumps(rec) + "\n")
    with pytest.raises(JournalCorrupt):
        j.read()
