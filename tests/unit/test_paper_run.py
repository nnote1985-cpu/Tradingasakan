import json

from scripts import paper_run


def _append(log, body):
    rec = {**body, "prev_hash": paper_run.last_hash(log)}
    rec["line_hash"] = paper_run.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode())
    with log.open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def test_chain_is_valid_and_detects_edits_and_removals(tmp_path):
    log = tmp_path / "runs.jsonl"
    for i in range(3):
        _append(log, {"n": i, "note": "ทดสอบ"})
    assert paper_run.verify_chain(log) == []

    lines = log.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["n"] = 99
    log.write_text("\n".join([lines[0], json.dumps(edited, sort_keys=True, ensure_ascii=False), lines[2]]) + "\n")
    assert any("line 2: line_hash" in p for p in paper_run.verify_chain(log))

    log.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert any("line 2: prev_hash" in p for p in paper_run.verify_chain(log))
