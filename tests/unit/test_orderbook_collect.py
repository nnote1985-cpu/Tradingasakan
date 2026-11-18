"""Order book collector: record shape, file layout, rollover compression, fetch failures."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("orderbook_collect", ROOT / "scripts" / "orderbook_collect.py")
ob = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["orderbook_collect"] = ob  # dataclass needs the module registered
_spec.loader.exec_module(ob)  # type: ignore[union-attr]

DAY1 = 1763424000000  # 2025-11-18 00:00:00 UTC
DAY2 = DAY1 + 86_400_000


def depth(bid: float = 100.0, ask: float = 100.2, n: int = 20) -> dict:
    return {
        "lastUpdateId": 42,
        "bids": [[f"{bid - i * 0.1:.2f}", "1.0" if i else "3.0"] for i in range(n)],
        "asks": [[f"{ask + i * 0.1:.2f}", "1.0"] for i in range(n)],
    }


def test_record_fields():
    r = ob.build_record(depth(), DAY1, DAY1 + 150, 20)
    assert r["t"] == DAY1 and r["lt"] == DAY1 + 150 and r["u"] == 42
    assert r["mid"] == pytest.approx(100.1)
    assert r["spr_bps"] == pytest.approx(0.2 / 100.1 * 1e4, abs=1e-3)
    assert r["imb5"] == pytest.approx((7 - 5) / 12, abs=1e-4)
    assert r["imb20"] == pytest.approx((22 - 20) / 42, abs=1e-4)
    assert len(r["b"]) == 20 and r["b"][0] == [100.0, 3.0] and r["a"][0] == [100.2, 1.0]


def test_levels_cut_and_bad_books():
    assert len(ob.build_record(depth(n=50), DAY1, DAY1, 20)["a"]) == 20
    assert ob.build_record({"bids": [], "asks": []}, DAY1, DAY1, 20) is None
    assert ob.build_record(depth(bid=101, ask=100), DAY1, DAY1, 20) is None  # crossed


def test_api_limit():
    assert ob.api_limit(20) == 20 and ob.api_limit(7) == 10 and ob.api_limit(500) == 100


def test_collect_writes_daily_jsonl_and_skips_failures(tmp_path):
    urls = []

    def fetch(url):
        urls.append(url)
        if "fapi" in url:
            raise TimeoutError("slow")
        return depth()

    c = ob.Collector(tmp_path, fetch=fetch)
    st = c.collect_slot(DAY1 + 10_000)
    assert st["spot"] == "ok" and st["perp"].startswith("error TimeoutError")
    assert any("api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20" in u for u in urls)
    lines = (tmp_path / "spot" / "BTCUSDT" / "2025-11-18.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["t"] == DAY1 + 10_000
    assert not (tmp_path / "perp" / "BTCUSDT" / "2025-11-18.jsonl").exists()


def test_rollover_gzips_previous_days_only(tmp_path):
    c = ob.Collector(tmp_path, markets=("spot",), fetch=lambda u: depth())
    c.collect_slot(DAY1)
    c.collect_slot(DAY1 + 10_000)
    c.collect_slot(DAY2)
    c.rollover("2025-11-19")
    folder = tmp_path / "spot" / "BTCUSDT"
    assert sorted(p.name for p in folder.iterdir()) == ["2025-11-18.jsonl.gz", "2025-11-19.jsonl"]
    with gzip.open(folder / "2025-11-18.jsonl.gz", "rt") as f:
        assert [json.loads(x)["t"] for x in f] == [DAY1, DAY1 + 10_000]


def test_rollover_keeps_existing_gz(tmp_path):
    c = ob.Collector(tmp_path, markets=("spot",), fetch=lambda u: depth())
    c.collect_slot(DAY1)
    c.rollover("2025-11-19")
    c.collect_slot(DAY1 + 20_000)  # late write to an already-compressed day
    c.rollover("2025-11-19")
    gz = sorted(p.name for p in (tmp_path / "spot" / "BTCUSDT").glob("2025-11-18*.gz"))
    assert len(gz) == 2


def test_run_loop_takes_slots(tmp_path):
    c = ob.Collector(tmp_path, markets=("spot",), fetch=lambda u: depth())
    ob.run(c, interval=1, max_slots=2)
    files = list((tmp_path / "spot" / "BTCUSDT").glob("*.jsonl"))
    assert sum(len(f.read_text().splitlines()) for f in files) == 2
