"""Both mirror scripts must survive a root that holds pathA-v1.1 and pathA-v1.2 books side by side."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V11 = {"rules_version": "pathA-v1.1", "start_equity": 10000.0, "trades": [], "history": [],
       "journal": [{"date": "2026-10-15", "at": "2026-10-16T08:21:00+00:00", "kind": "open", "weight": 1.0, "notes": []}]}
V12 = {"format": "rebalance_book.v1", "rules_version": "pathA-v1.2", "start_equity": "10000", "history": [],
       "book": {"currency": "USDT", "entries": [{"key": "open", "at": "2026-10-16T08:21:00+00:00", "kind": "open",
                                                 "lines": [["cash", "10000"], ["capital", "-10000"]],
                                                 "data": {"amount": "10000"}}]},
       "buy_and_hold": {"currency": "USDT", "entries": []}}


def _root(tmp_path, *states):
    for s in states:
        (tmp_path / s["rules_version"]).mkdir()
        (tmp_path / s["rules_version"] / "state.json").write_text(json.dumps(s))
    return str(tmp_path)


def _run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True)


def test_paper_mirror_skips_v12(tmp_path):
    r = _run("supabase_sync_sql.py", "--root", _root(tmp_path, V11, V12))
    assert r.returncode == 0, r.stderr
    assert "pathA-v1.1" in r.stdout and "pathA-v1.2" not in r.stdout
    assert "not mirrored" in r.stderr


def test_trading_mirror_handles_both_formats(tmp_path):
    r = _run("trading_sync_sql.py", "--root", _root(tmp_path, V11, V12))
    assert r.returncode == 0, r.stderr
    assert "'pathA-v1.2/paper:open'" in r.stdout and "'owner_capital'" in r.stdout
    assert "'34028a6'" in r.stdout and "'5306f62'" in r.stdout


def test_trading_mirror_refuses_an_unbalanced_entry(tmp_path):
    bad = json.loads(json.dumps(V12))
    bad["book"]["entries"][0]["lines"][1][1] = "-9999"
    r = _run("trading_sync_sql.py", "--root", _root(tmp_path, bad))
    assert r.returncode != 0 and "does not balance" in r.stderr


def test_trading_mirror_names_a_missing_commit(tmp_path):
    r = _run("trading_sync_sql.py", "--root", _root(tmp_path, {**V11, "rules_version": "pathA-v9"}))
    assert r.returncode != 0 and "--commit pathA-v9=" in r.stderr


def perp_state() -> dict:
    """A real futures-r7 book with flips both ways and funding (the book's own test market)."""
    import importlib.util

    import numpy as np
    import pandas as pd

    spec = importlib.util.spec_from_file_location("r7_tests", ROOT / "tests/unit/test_trend_perp_book.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    prices = np.r_[np.linspace(100, 200, 130), np.linspace(200, 120, 60), np.linspace(120, 150, 40)]
    daily, hourly = m._market(prices)
    book = m._run(daily, hourly, pd.Series(0.001, index=daily.index))
    return json.loads(json.dumps(book.to_dict()))


def test_trading_mirror_copies_the_futures_r7_perp_journal(tmp_path):
    from decimal import Decimal

    state = perp_state()
    kinds = {e["kind"] for e in state["book"]["entries"]}
    assert {"open", "fill", "funding"} <= kinds
    r = _run("trading_sync_sql.py", "--root", _root(tmp_path, state))
    assert r.returncode == 0, r.stderr
    assert "'futures-r7/paper'" in r.stdout and "'futures-r7/buy_and_hold'" in r.stdout and "'39488df'" in r.stdout
    n = len(state["book"]["entries"]) + len(state["buy_and_hold"]["entries"])
    assert r.stdout.count("insert into trading.journal_entries") == n
    assert "'futures-r7/paper:" in r.stdout and "funding rate" in r.stdout
    # a short day carries negative exposure
    short = next(h for h in state["history"] if h["side_held_at_close"] == -1)
    assert "'-" + str((Decimal(short["exposure"])).quantize(Decimal("1e-12"))) + "'" in r.stdout
    s = _run("supabase_sync_sql.py", "--root", str(tmp_path))
    assert s.returncode == 0 and "futures-r7 (trend_perp_book.v1) is not mirrored" in s.stderr
