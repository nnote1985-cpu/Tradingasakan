"""spot-e1-v1 in the trading.* mirror: fills rebuilt from the replayed trades balance and land on the
book's own cash; daily run reports become equity snapshots; the paper.* mirror skips the book."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

from trading.strategies.argus_e1 import replay

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("e1_tests", ROOT / "tests/unit/test_e1_book.py")
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
_pt = importlib.util.spec_from_file_location("paper_trade_e1", ROOT / "scripts/paper_trade_e1.py")
pt = importlib.util.module_from_spec(_pt)
_pt.loader.exec_module(pt)


def _state(open_at_end: bool) -> tuple[dict, dict]:
    h1, m1 = _m.synthetic(160, 2)
    start = pd.Timestamp("2024-02-15", tz="UTC")
    for end_day in pd.date_range("2024-05-01", "2024-06-08", tz="UTC"):
        r = replay(h1, h1, m1, start, end_day)
        if len(r["trades"]) >= 2 and (r["open_position"] is not None) == open_at_end:
            break
    else:
        raise AssertionError("no window with the wanted shape")
    state = {"rules_version": "spot-e1-v1", "start": "2024-02-15", "through": str((end_day - pd.Timedelta(days=1)).date()),
             "trades": pt.jsonable(r["trades"]), "events": pt.jsonable(r["events"]), "halted": r["halted"],
             "open_position": pt.jsonable(r["open_position"])}
    return state, r


def _write(tmp_path: Path, state: dict, reports: list[dict]) -> str:
    book = tmp_path / "spot-e1-v1"
    (book / "daily").mkdir(parents=True)
    (book / "state.json").write_text(json.dumps(state))
    for rep in reports:
        (book / "daily" / f"{rep.get('data_through', 'x')}.json").write_text(json.dumps(rep))
    return str(tmp_path)


def _run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True)


def _lines(sql: str, label: str) -> list[tuple[str, Decimal]]:
    out = []
    for stmt in sql.splitlines():
        if "insert into trading.journal_lines" in stmt and f"label = '{label}'" in stmt:
            out += [(a, Decimal(x)) for a, x in re.findall(r"\d+, '([a-z_]+)', '(-?[0-9.]+)'::numeric", stmt)]
    return out


def test_e1_fills_balance_and_match_the_book_cash(tmp_path):
    for open_at_end in (False, True):
        state, r = _state(open_at_end)
        root = tmp_path / str(open_at_end)
        root.mkdir()
        res = _run("trading_sync_sql.py", "--root", _write(root, state, []))
        assert res.returncode == 0, res.stderr
        assert "values ('spot-e1-v1', 'f1b9247', 'frozen/spot-e1-v1')" in res.stdout
        lines = _lines(res.stdout, "spot-e1-v1/paper")
        assert sum(x for _, x in lines) == 0
        cash = sum(x for a, x in lines if a == "cash")
        assert abs(float(cash) - r["cash"]) < 1e-6
        n_fills = 2 * len(state["trades"]) + (1 if open_at_end else 0)
        assert res.stdout.count("insert into trading.journal_entries") == n_fills + 2      # + two starting-cash entries
        again = _run("trading_sync_sql.py", "--root", str(root))
        assert again.stdout == res.stdout                                                   # deterministic keys


def test_e1_reports_become_snapshots_and_problem_reports_are_skipped(tmp_path):
    state, _ = _state(False)
    good = {"data_through": "2024-05-01", "equity": 10123.4, "buy_and_hold": 9876.5, "weight_held": 0.0, "problems": []}
    bad = {"data_through": "2024-05-02", "equity": 1.0, "buy_and_hold": 1.0, "weight_held": 0.0, "problems": ["mismatch"]}
    res = _run("trading_sync_sql.py", "--root", _write(tmp_path, state, [good, bad]))
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("insert into trading.equity_snapshots") == 2
    assert "'2024-05-01T23:59:59.999+00:00', '10123.400000000000'" in res.stdout
    assert "'9876.500000000000'" in res.stdout and "2024-05-02T23" not in res.stdout
    p = _run("supabase_sync_sql.py", "--root", str(tmp_path))
    assert p.returncode == 0 and "spot-e1-v1 is not mirrored to paper.*" in p.stderr


def test_run_record_saves_the_summary_and_prints_the_audit(tmp_path):
    book = tmp_path / "spot-e1-v1"
    book.mkdir()
    before = tmp_path / "before"
    before.write_text(_run("e1_run_record.py", "--book", str(book), "--before").stdout)
    assert before.read_text().strip() == "none"
    (book / "state.json").write_text('{"rules_version": "spot-e1-v1"}')
    out = tmp_path / "out"
    out.write_text(json.dumps({"data_through": "2026-11-13", "equity": 10000.0, "problems": []}) + "\n\nข้อความ\n")
    r = _run("e1_run_record.py", "--book", str(book), "--out", str(out), "--before-file", str(before))
    assert r.returncode == 0, r.stderr
    assert json.loads((book / "daily" / "2026-11-13.json").read_text())["equity"] == 10000.0
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("insert into trading.audit_events") and "'paper_run', 'spot-e1-v1', 'opened', 'none'," in last
    out.write_text(json.dumps({"rules_version": "spot-e1-v1", "new_entries": [], "start": "2026-11-14", "problems": []}) + "\n\nเปิด\n")
    r = _run("e1_run_record.py", "--book", str(book), "--out", str(out), "--before-file", str(before))
    assert json.loads(r.stdout.splitlines()[0])["saved"] is None
