"""Smoke test: the spike scripts run end to end on SYNTHETIC bars.

Synthetic data only checks plumbing; its numbers are never research results.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SPIKE = Path(__file__).resolve().parents[2] / "research" / "spike"
sys.path.insert(0, str(SPIKE))

import run_backtest  # noqa: E402
import run_power  # noqa: E402


def _write_synthetic(d: Path, hours: int = 24 * 420) -> None:
    rng = np.random.default_rng(11)
    idx = pd.date_range("2023-01-01", periods=hours, freq="1h", tz="UTC")
    drift = np.sin(np.arange(hours) / 900) * 0.0008
    close = 30_000 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, hours)))
    open_ = np.concatenate(([close[0]], close[:-1]))
    spread = np.abs(rng.normal(0, 0.003, hours)) * close
    bars = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close,
                         "volume": 1.0}, index=idx)
    bars.to_parquet(d / "BTCUSDT_1h.parquet")
    f = pd.Series(0.0001, index=pd.date_range(idx[0], idx[-1], freq="8h", tz="UTC"), name="funding_rate")
    f.to_frame().to_parquet(d / "BTCUSDT_funding.parquet")


def test_spike_scripts_run(tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    _write_synthetic(data)
    cfg = {
        "symbol": "BTCUSDT", "data_dir": str(data), "exec_interval": "1m",
        "partitions": {"development": 0.5, "validation": 0.25, "holdout": 0.25},
        "initial_equity": 10000,
        "cost_grid": {"fee_mult": [1.0, 2.0], "slip_mult": [1.0]},
        "param_grid": {"stop_atr_mult": [2.0], "take_profit_r": [2.0],
                       "time_exit_hours": [24], "breakout_lookback": [20]},
        "power": {"durations_days": [30], "n_windows": 2, "seeds": 1, "alpha": 0.05,
                  "delta_floor": 0.0, "block_days": 7, "reps": 50},
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))

    monkeypatch.setattr(sys, "argv", ["x", "--config", str(cfg_path), "--out", str(out)])
    run_backtest.main()
    report = json.loads((out / "report.json").read_text())
    assert report["exec_interval"] == "1h"          # 1m missing → 1h fallback
    assert report["holdout_simulated"] is False
    assert "development/conservative/absorbing" in report["runs"]
    hold_start = pd.Timestamp(report["partitions"]["holdout"][0])
    for part in ("development", "validation"):
        tr = pd.read_csv(out / f"trades_{part}.csv", parse_dates=["exit_time"])
        assert (tr["exit_time"] <= hold_start).all()
    assert len(pd.read_csv(out / "trials.csv")) == 8 + 4 + 4

    run_power.main()
    verdict = json.loads((out / "power.json").read_text())["verdict"]
    assert verdict["decision"] in {"CONTINUE", "REVISE_HYPOTHESIS", "INSUFFICIENT_DATA"}
