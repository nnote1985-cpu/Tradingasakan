"""spot-e1-v1 book (docs/e1-paper-book-plan.md): same rule as R13, stable replay, HALT."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.strategies import argus_e1
from trading.strategies.argus_e1 import replay

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "spike"))


def synthetic(days: int, seed: int, drift: float = 0.00002):
    rng = np.random.default_rng(seed)
    n = days * 1440
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    steps = rng.normal(drift, 0.0012, n)
    steps += np.repeat(rng.normal(0, 0.004, days * 6), 240) / 240     # 4h regimes so breakouts happen
    close = 30_000 * np.exp(np.cumsum(steps))
    opn = np.r_[30_000, close[:-1]]
    wig = np.abs(rng.normal(0, 0.0005, n))
    m1 = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) * (1 + wig), "low": np.minimum(opn, close) * (1 - wig),
                       "close": close, "volume": 1.0}, index=idx)
    g = m1.resample("1h")
    h1 = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                       "volume": g["volume"].sum()})
    return h1, m1


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_same_trades_as_research_port(seed):
    import ea_ports

    h1, m1 = synthetic(160, seed)
    start, end = pd.Timestamp("2024-02-15", tz="UTC"), pd.Timestamp("2024-06-08", tz="UTC")
    d, tr, _ = ea_ports.simulate(ea_ports.E1Donchian, h1, m1, None, start="2024-02-15", end="2024-06-08", market="spot", sizing="native",
                                 costs=ea_ports.SPOT)
    r = replay(h1, h1, m1, start, end, halt=False)
    a = tr[tr["reason"] != "window_end"].reset_index(drop=True)
    assert len(a) > 0
    b = pd.DataFrame(r["trades"])[a.columns]
    pd.testing.assert_frame_equal(a.drop(columns="tp0"), b.drop(columns="tp0"), check_dtype=False)
    e = r["equity_daily"]
    np.testing.assert_allclose(e.to_numpy()[:-1], d.to_numpy()[: len(e) - 1], rtol=1e-12)


def test_replay_is_stable_day_to_day():
    h1, m1 = synthetic(160, 4)
    start = pd.Timestamp("2024-02-15", tz="UTC")
    a = replay(h1, h1, m1, start, pd.Timestamp("2024-05-01", tz="UTC"))
    b = replay(h1, h1, m1, start, pd.Timestamp("2024-05-02", tz="UTC"))
    assert b["trades"][: len(a["trades"])] == a["trades"]
    assert len(b["trades"]) >= len(a["trades"])


def test_warmup_window_does_not_change_trades():
    h1, m1 = synthetic(400, 5)
    start, end = pd.Timestamp("2024-09-01", tz="UTC"), pd.Timestamp("2025-02-01", tz="UTC")
    a = replay(h1[h1.index >= start - pd.Timedelta(days=120)], h1, m1, start, end)
    b = replay(h1[h1.index >= start - pd.Timedelta(days=240)], h1, m1, start, end)
    assert a["trades"] == b["trades"]


def test_halt_after_25pct_drawdown(monkeypatch):
    days = 60
    idx = pd.date_range("2024-01-01", periods=days * 1440, freq="1min", tz="UTC")
    px = np.full(len(idx), 100.0)
    gap_at = idx.get_loc(pd.Timestamp("2024-01-20 05:30", tz="UTC"))
    px[gap_at:] = 70.0                                      # one-minute gap of -30% through the stop
    m1 = pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": 1.0}, index=idx)
    g = m1.resample("1h")
    h1 = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    monkeypatch.setattr(argus_e1.E1Signals, "entry_stop", lambda self, b: self.c[b] * 0.995)
    monkeypatch.setattr(argus_e1.E1Signals, "trail", lambda self, b, sl: None)
    r = replay(h1, h1, m1, pd.Timestamp("2024-01-20", tz="UTC"), pd.Timestamp("2024-02-01", tz="UTC"))
    kinds = [e["kind"] for e in r["events"]]
    assert r["halted"] and "halt" in kinds
    assert len(r["trades"]) == 1 and r["trades"][0]["reason"] == "stop"
    assert "open" not in kinds[kinds.index("halt"):]


def test_complete_through_stops_at_first_gap():
    spec = importlib.util.spec_from_file_location("paper_trade_e1", ROOT / "scripts" / "paper_trade_e1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h1, m1 = synthetic(5, 6)
    assert mod.complete_through(h1, m1, h1, date(2024, 1, 1), date(2024, 1, 5)) == date(2024, 1, 5)
    m1b = m1.drop(m1.index[3 * 1440 + 10])
    assert mod.complete_through(h1, m1b, h1, date(2024, 1, 1), date(2024, 1, 5)) == date(2024, 1, 3)
    assert mod.complete_through(h1, m1b, h1, date(2024, 1, 4), date(2024, 1, 5)) is None
