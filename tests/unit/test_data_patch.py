"""Data patches (docs/data-patches.md): rows the archive lacks are added, archive rows always win,
continued versions copy the base state byte for byte and sync under their own names."""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import continue_book, paper_run

ROOT = Path(__file__).resolve().parents[2]
U = "https://data.binance.vision/data/futures/um"
D27, D28, D29 = "1793059200000", "1793145600000", "1793232000000"


def _row(t: str, close: str = "1") -> list[str]:
    return [t, "1", "2", "0.5", close, "10", str(int(t) + 86_399_999), "10", "5", "4", "4", "0"]


PATCH = {"id": "gapT", "sha256": "f" * 64, "market": "futures/um", "symbol": "BTCUSDT", "kind": "klines",
         "days": ["2026-10-28"], "columns": ["open_time", "open", "high", "low", "close", "volume", "close_time",
                                             "quote_volume", "count", "taker_buy_volume",
                                             "taker_buy_quote_volume", "ignore"],
         "rows": {"1d": [_row(D28, "81308.8")]}}


def _zip(text: str, name: str = "BTCUSDT-1d-2026-10.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def _lines(blob: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0]).decode().splitlines()


def test_monthly_file_gets_the_missing_day_in_order():
    header = ",".join(PATCH["columns"])
    blob = _zip("\n".join([header, ",".join(_row(D27)), ",".join(_row(D29))]) + "\n")
    out, info = paper_run.apply_patch(f"{U}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10.zip", blob, PATCH)
    assert info["rows_added"] == 1 and info["archive_sha256"] == paper_run.sha256(blob)
    lines = _lines(out)
    assert lines[0] == header and [ln.split(",")[0] for ln in lines[1:]] == [D27, D28, D29]
    assert lines[2].split(",")[4] == "81308.8"
    again, _ = paper_run.apply_patch(f"{U}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10.zip", blob, PATCH)
    assert paper_run.sha256(again) == paper_run.sha256(out)            # deterministic


def test_archive_row_always_wins():
    blob = _zip("\n".join([",".join(_row(D27)), ",".join(_row(D28, "99999")), ",".join(_row(D29))]) + "\n")
    out, info = paper_run.apply_patch(f"{U}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10.zip", blob, PATCH)
    assert out is blob and info["rows_added"] == 0


def test_missing_daily_file_is_built_from_the_patch():
    out, info = paper_run.apply_patch(f"{U}/daily/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10-28.zip", None, PATCH)
    assert info["archive_sha256"] is None and info["rows_added"] == 1
    assert [ln.split(",")[0] for ln in _lines(out)[1:]] == [D28]


@pytest.mark.parametrize("url", [f"{U}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-09.zip",
                                 f"{U}/daily/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10-29.zip",
                                 f"{U}/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10.zip",
                                 "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-10.zip"])
def test_other_files_are_untouched(url):
    blob = _zip("x\n")
    assert paper_run.apply_patch(url, blob, PATCH) == (blob, None)
    assert paper_run.apply_patch(url, None, PATCH) == (None, None)


def test_gap1_patch_is_the_checked_rebuild():
    p = paper_run.load_patch("gap1")
    assert p["days"] == ["2026-10-28"] and len(p["rows"]["1h"]) == 24
    assert p["rows"]["1d"][0][:5] == [D28, "80868.4", "81800.0", "80355.4", "81308.8"]
    assert p["check"]["day"] == "2026-10-27" and "equal the archive" in p["check"]["result"]


def _base(root: Path, name: str, state: dict) -> None:
    (root / name).mkdir()
    (root / name / "state.json").write_text(json.dumps(state))
    log = root / name / "runs.jsonl"
    rec = {"book": name, "code_commit": "abc", "prev_hash": None}
    rec["line_hash"] = paper_run.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode())
    log.write_text(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


V12 = {"format": "rebalance_book.v1", "rules_version": "pathA-v1.2", "start_equity": "10000", "history": [],
       "book": {"currency": "USDT", "entries": [{"key": "open", "at": "2026-10-16T08:21:00+00:00", "kind": "open",
                                                 "lines": [["cash", "10000"], ["capital", "-10000"]],
                                                 "data": {"amount": "10000"}}]},
       "buy_and_hold": {"currency": "USDT", "entries": []}}


def test_continue_copies_state_and_marks_the_base(tmp_path, capsys):
    _base(tmp_path, "pathA-v1.2", V12)
    before = (tmp_path / "pathA-v1.2" / "state.json").read_bytes()
    assert continue_book.main(["--book", "pathA-v1.2", "--as", "g1", "--root", str(tmp_path)]) == 0
    new = tmp_path / "pathA-v1.2-g1"
    assert (new / "state.json").read_bytes() == before
    assert (tmp_path / "pathA-v1.2" / "state.json").read_bytes() == before
    m = json.loads((new / "continued_from.json").read_text())
    assert m["base_book"] == "pathA-v1.2" and m["data_patch"]["id"] == "gap1"
    assert m["base_state_sha256"] == paper_run.sha256(before) and m["base_last_code_commit"] == "abc"
    assert json.loads((tmp_path / "pathA-v1.2" / "superseded_by.json").read_text())["book"] == "pathA-v1.2-g1"
    assert "'paper_continue', 'pathA-v1.2-g1'" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        continue_book.main(["--book", "pathA-v1.2", "--as", "g1", "--root", str(tmp_path)])


def test_continue_refuses_a_broken_chain(tmp_path):
    _base(tmp_path, "pathA-v1.2", V12)
    log = tmp_path / "pathA-v1.2" / "runs.jsonl"
    log.write_text(log.read_text().replace('"abc"', '"xyz"'))
    with pytest.raises(SystemExit, match="chain is broken"):
        continue_book.main(["--book", "pathA-v1.2", "--as", "g1", "--root", str(tmp_path)])
    assert not (tmp_path / "pathA-v1.2-g1").exists()


def test_continued_version_syncs_under_its_own_name(tmp_path):
    _base(tmp_path, "pathA-v1.2", V12)
    continue_book.main(["--book", "pathA-v1.2", "--as", "g1", "--root", str(tmp_path)])
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "trading_sync_sql.py"), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "'pathA-v1.2-g1/paper:open'" in r.stdout and "'pathA-v1.2/paper:open'" in r.stdout
    assert "values ('pathA-v1.2-g1', '34028a6', 'frozen/pathA-v1.2')" in r.stdout


def test_continued_v11_mirrors_to_paper_under_its_own_name(tmp_path):
    v11 = {"rules_version": "pathA-v1.1", "start_equity": 10000.0, "trades": [], "history": [],
           "journal": [{"date": "2026-10-15", "at": "2026-10-16T08:21:00+00:00", "kind": "open",
                        "weight": 1.0, "notes": []}]}
    _base(tmp_path, "pathA-v1.1", v11)
    continue_book.main(["--book", "pathA-v1.1", "--as", "g1", "--root", str(tmp_path)])
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "supabase_sync_sql.py"), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "('pathA-v1.1-g1', 'frozen/pathA-v1.1'" in r.stdout
