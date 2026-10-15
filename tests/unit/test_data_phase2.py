"""Phase 2 acceptance tests (docs/phase-2-data.md): gap, no-lookahead, revisions, replay, checksum."""

import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from trading.data.collector import DATASETS, ChecksumMismatch, Collector
from trading.data.point_in_time import bars, gaps, resample_4h
from trading.data.raw_store import RawStore
from trading.data.snapshot import SnapshotLog, build_snapshot, replay

SYM = "BTCUSDT"
D0 = date(2026, 9, 1)
H0 = date(2026, 8, 1)          # snapshot tests: 41 days through 09-10 cover the 30-day lookback
N = 41
LAST = H0 + timedelta(days=N - 1)


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _zip(name, text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def _klines(day, price=lambda h: 100.0 + h, skip=()):
    rows = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"]
    t0 = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    for h in range(24):
        if h in skip:
            continue
        p = price(h)
        rows.append(f"{t0 + h * 3600_000},{p},{p + 1},{p - 1},{p + 0.5},10,{t0 + (h + 1) * 3600_000 - 1},1000,5,4,400,0")
    return _zip("k.csv", "\n".join(rows) + "\n")


def _funding(year, month, days, rate=0.0001):
    rows = ["calc_time,funding_interval_hours,last_funding_rate"]
    for d in range(1, days + 1):
        for h in (0, 8, 16):
            rows.append(f"{int(pd.Timestamp(year=year, month=month, day=d, hour=h, tz='UTC').timestamp() * 1000)},8,{rate}")
    return _zip("f.csv", "\n".join(rows) + "\n")


class Archive:
    """A fake data.binance.vision: url -> zip bytes, with .CHECKSUM files."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.bad_checksum: set[str] = set()

    def add(self, url, blob):
        self.files[url] = blob

    def __call__(self, url):
        if url.endswith(".CHECKSUM"):
            u = url[: -len(".CHECKSUM")]
            if u not in self.files:
                return None
            sha = "0" * 64 if u in self.bad_checksum else hashlib.sha256(self.files[u]).hexdigest()
            return f"{sha}  {u.rsplit('/', 1)[1]}\n".encode()
        return self.files.get(url)


def _fill(arc, days, skip_day=None, price=lambda h: 100.0 + h, skip_premium=None, start=D0):
    for i in range(days):
        d = start + timedelta(days=i)
        if d == skip_day:
            continue
        for name in ("perp_klines_1h", "spot_klines_1h"):
            arc.add(DATASETS[name].url(SYM, d), _klines(d, price))
        if d != skip_premium:
            arc.add(DATASETS["premium_index_1h"].url(SYM, d), _klines(d, lambda h: 0.00005 - 0.5))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def world(tmp_path):
    arc, store = Archive(), RawStore(tmp_path / "raw")
    clock = Clock(T("2026-09-11T08:10:00"))
    return arc, store, clock, Collector(store, fetch=arc, clock=clock)


def test_collector_stores_verified_files_once(world):
    arc, store, clock, col = world
    _fill(arc, 2)
    first = col.collect(SYM, D0, D0 + timedelta(days=2), ["perp_klines_1h"])
    assert [r["status"] for r in first] == ["STORED", "STORED", "MISSING"]
    again = col.collect(SYM, D0, D0 + timedelta(days=1), ["perp_klines_1h"])
    assert [r["status"] for r in again] == ["DUPLICATE", "DUPLICATE"]
    assert len(store.records()) == 2 and store.verify() == []


def test_checksum_mismatch_stores_nothing(world):
    arc, store, clock, col = world
    _fill(arc, 1)
    arc.bad_checksum.add(DATASETS["perp_klines_1h"].url(SYM, D0))
    with pytest.raises(ChecksumMismatch):
        col.collect_file(DATASETS["perp_klines_1h"], SYM, D0)
    assert store.records() == []


def test_gap_is_reported_and_not_filled(world):
    arc, store, clock, col = world
    _fill(arc, 3, skip_day=D0 + timedelta(days=1))
    arc.add(DATASETS["perp_klines_1h"].url(SYM, D0), _klines(D0, skip=(5,)))
    col.collect(SYM, D0, D0 + timedelta(days=2), ["perp_klines_1h"])
    df, _ = bars(store, "perp_klines_1h", SYM, T("2026-09-12T00:00:00"))
    start = pd.Timestamp(D0, tz="UTC")
    missing = gaps(df, start, start + pd.Timedelta(days=3))
    assert len(df) == 47 and len(missing) == 25
    assert missing[0] == "2026-09-01T05:00:00+00:00"
    h4 = resample_4h(df)
    assert pd.Timestamp("2026-09-01 04:00", tz="UTC") not in h4.index     # 1h bar 05:00 missing
    assert pd.Timestamp("2026-09-01 08:00", tz="UTC") in h4.index
    assert not any(h4.index.floor("1D") == pd.Timestamp("2026-09-02", tz="UTC"))


def test_no_lookahead_on_bar_close_and_on_file_arrival(world):
    arc, store, clock, col = world
    _fill(arc, 2)
    clock.t = T("2026-09-02T08:10:00")                     # day 1 arrives next morning
    col.collect(SYM, D0, D0, ["perp_klines_1h"])
    clock.t = T("2026-09-03T08:10:00")
    col.collect(SYM, D0 + timedelta(days=1), D0 + timedelta(days=1), ["perp_klines_1h"])
    # Before day 1's file arrived nothing of day 1 is visible, though its bars had closed.
    df, _ = bars(store, "perp_klines_1h", SYM, T("2026-09-02T08:00:00"))
    assert df.empty
    df, _ = bars(store, "perp_klines_1h", SYM, T("2026-09-02T08:10:00"))
    assert len(df) == 24 and (df["available_at"] <= pd.Timestamp("2026-09-02 08:10", tz="UTC")).all()
    # Day 2's file is in the store at 09-03 08:10; a view at 09-02 12:00 must not see it.
    df, _ = bars(store, "perp_klines_1h", SYM, T("2026-09-02T12:00:00"))
    assert df.index.max() == pd.Timestamp("2026-09-01 23:00", tz="UTC")


def test_bars_not_visible_before_they_close(tmp_path):
    store = RawStore(tmp_path / "raw")
    blob = _klines(D0)
    store.put("u", blob, T("2026-09-01T00:30:00"), "VERIFIED", meta={"dataset": "perp_klines_1h", "symbol": SYM, "day": "2026-09-01"})
    df, _ = bars(store, "perp_klines_1h", SYM, T("2026-09-01T03:00:00"))
    assert list(df.index.hour) == [0, 1, 2]
    assert (df["close_at"] <= pd.Timestamp("2026-09-01 03:00", tz="UTC")).all()


def _history(arc, col, clock, at, funding_days=7, **kw):
    _fill(arc, N, start=H0, **kw)
    arc.add(DATASETS["funding"].url(SYM, H0), _funding(2026, 8, 31))
    arc.add(DATASETS["funding"].url(SYM, D0), _funding(2026, 9, funding_days))
    clock.t = at
    col.collect(SYM, H0, LAST)


def test_revision_keeps_old_snapshot_replayable(world, tmp_path):
    arc, store, clock, col = world
    _history(arc, col, clock, T("2026-09-11T08:10:00"), funding_days=9)
    as_of = T("2026-09-11T09:00:00")
    s1 = build_snapshot(store, SYM, as_of)
    log = SnapshotLog(tmp_path / "snapshots.jsonl")
    log.append(s1, taken_at=as_of)

    # Binance republishes day 10 with a corrected price at 09-12.
    url = DATASETS["perp_klines_1h"].url(SYM, LAST)
    arc.add(url, _klines(LAST, lambda h: 200.0 + h))
    clock.t = T("2026-09-12T10:00:00")
    r = col.collect_file(DATASETS["perp_klines_1h"], SYM, LAST)
    assert r["status"] == "REVISION" and r["revision"] == 2
    assert [x.revision for x in store.records(url)] == [1, 2]

    assert replay(store, s1)["ok"]                                    # still built from revision 1
    assert log.replay_all(store) == [{"as_of": s1["as_of"], "ok": True, "recorded": s1["sha256"], "replayed": s1["sha256"]}]
    s2 = build_snapshot(store, SYM, T("2026-09-12T10:00:00"))
    assert s2["last_bar"]["perp_1h"]["close"] == 223.5
    assert {"source_key": url, "revision": 2, "sha256": r["sha256"]} in s2["raw_pointers"]
    assert s1["last_bar"]["perp_1h"]["close"] == 123.5


def test_funding_policy_actual_then_proxy(world):
    arc, store, clock, col = world
    _history(arc, col, clock, T("2026-09-11T08:10:00"))       # actual prints through 09-07 16:00
    s = build_snapshot(store, SYM, T("2026-09-11T09:00:00"))
    f = s["funding"]
    assert [w["source"] for w in f["window"]] == ["actual"] * 4 + ["proxy"] * 3
    assert f["last_actual_print"] == "2026-09-07T16:00:00+00:00"
    # Proxy print: P = 0.00005, F = P + clamp(0.0001 - P) = 0.0001, three a day, same as the actual prints.
    assert f["mean_7d_per_day"] == pytest.approx(0.0003) and f["hot"] is False and f["proxy_days"] == 3
    assert s["quality"]["complete"], s["quality"]["problems"]
    assert s["features"]["basis"] == 0.0 and "ema20_4h" in s["features"]
    body = {k: v for k, v in s.items() if k != "sha256"}
    assert s["sha256"] == hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_missing_funding_is_flagged_not_zero(world):
    arc, store, clock, col = world
    _history(arc, col, clock, T("2026-09-11T08:10:00"), skip_premium=LAST)
    s = build_snapshot(store, SYM, T("2026-09-11T09:00:00"))
    f = s["funding"]
    assert [w["source"] for w in f["window"]] == ["actual"] * 4 + ["proxy"] * 2 + ["missing"]
    assert f["window"][-1]["funding"] is None and f["mean_7d_per_day"] is None and f["hot"] is None
    assert not s["quality"]["complete"]
    assert "premium_index_1h" in s["quality"]["gaps"]
    assert any("funding incomplete on 2026-09-10" in p for p in s["quality"]["problems"])


def test_stale_data_is_flagged(world):
    arc, store, clock, col = world
    _history(arc, col, clock, T("2026-09-11T08:10:00"))
    s = build_snapshot(store, SYM, T("2026-09-14T00:00:00"))
    assert s["quality"]["stale"]["perp_klines_1h"]["stale"] is True
    assert not s["quality"]["complete"]


def test_verify_detects_tampering(world):
    arc, store, clock, col = world
    _fill(arc, 1)
    col.collect(SYM, D0, D0, ["perp_klines_1h", "spot_klines_1h"])
    rec = store.records()[0]
    blob_path = store.root / "blobs" / rec.sha256[:2] / rec.sha256
    blob_path.write_bytes(b"changed")
    assert any("blob changed" in p for p in store.verify())
    lines = store.log_path.read_text().splitlines()
    d = json.loads(lines[1])
    d["received_at"] = "2026-01-01T00:00:00+00:00"
    store.log_path.write_text(lines[0] + "\n" + json.dumps(d) + "\n")
    assert any("line 2: line hash" in p for p in store.verify())


def test_naive_timestamps_are_refused(tmp_path):
    store = RawStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.put("u", b"x", datetime(2026, 9, 1), "VERIFIED")
    with pytest.raises(ValueError):
        build_snapshot(store, SYM, datetime(2026, 9, 1))


def test_short_history_is_flagged(world):
    arc, store, clock, col = world
    _fill(arc, 3)
    clock.t = T("2026-09-04T08:10:00")
    col.collect(SYM, D0, D0 + timedelta(days=2), ["perp_klines_1h", "spot_klines_1h", "premium_index_1h"])
    s = build_snapshot(store, SYM, T("2026-09-04T09:00:00"))
    assert s["quality"]["short_history"]["perp_klines_1h"] == "2026-09-01T00:00:00+00:00"
    assert "ema20_4h" not in s["features"] and not s["quality"]["complete"]
