"""Power / design simulation for the v1 AI-filter experiment — BUILDPLAN v4.2 §17.3.

Filters here are synthetic. ORACLE_SYNTHETIC filters read future outcomes and
exist only to measure what the evaluation method can detect; they must never be
routed to trading or reported as a model that is achievable.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.evaluation.bootstrap import lower_bound
from trading.evaluation.metrics import daily_log_returns
from trading.simulator.bar_backtest import BarBacktester, Candidate


@dataclass(frozen=True)
class FilterSpec:
    name: str
    kind: str  # "NULL_RANDOM" | "ORACLE_SYNTHETIC"
    veto_prob: float = 0.0          # NULL_RANDOM
    loser_recall: float = 0.0       # ORACLE_SYNTHETIC: P(veto | standalone loser)
    winner_false_veto: float = 0.0  # ORACLE_SYNTHETIC: P(veto | standalone winner)


def _uniform(seed: int, cand_id: str) -> float:
    return float(np.random.default_rng([seed, zlib.crc32(cand_id.encode())]).random())


def make_filter(spec: FilterSpec, seed: int, labels: dict[str, float]):
    def fn(cand: Candidate) -> bool:
        u = _uniform(seed, cand.cand_id)
        if spec.kind == "NULL_RANDOM":
            return u >= spec.veto_prob
        r = labels.get(cand.cand_id, np.nan)
        if not np.isfinite(r):
            return True
        p = spec.loser_recall if r < 0 else spec.winner_false_veto
        return u >= p
    return fn


def default_filters(base_veto_rate: float = 0.2) -> list[FilterSpec]:
    return [
        FilterSpec("null_random", "NULL_RANDOM", veto_prob=base_veto_rate),
        FilterSpec("oracle_weak", "ORACLE_SYNTHETIC", loser_recall=0.30, winner_false_veto=0.15),
        FilterSpec("oracle_moderate", "ORACLE_SYNTHETIC", loser_recall=0.40, winner_false_veto=0.10),
        FilterSpec("oracle_strong", "ORACLE_SYNTHETIC", loser_recall=0.60, winner_false_veto=0.10),
        FilterSpec("oracle_very_strong", "ORACLE_SYNTHETIC", loser_recall=0.80, winner_false_veto=0.05),
    ]


def run_power_design(bt: BarBacktester, *, dev_start, dev_end, durations_days=(90, 180, 365),
                     filters: list[FilterSpec] | None = None, n_windows: int = 12,
                     seeds: int = 5, alpha: float = 0.05, delta_floor: float = 0.0,
                     block_days: int = 7, reps: int = 1000) -> dict:
    dev_start, dev_end = pd.Timestamp(dev_start), pd.Timestamp(dev_end)
    labels_df = bt.standalone_outcomes(start=dev_start, end=dev_end)
    labels = dict(zip(labels_df["cand_id"], labels_df["r_multiple"]))
    filters = filters or default_filters()

    rows = []
    for dur in durations_days:
        span = pd.Timedelta(days=dur)
        last_start = dev_end - span
        if last_start <= dev_start:
            continue
        starts = pd.date_range(dev_start.normalize(), last_start.normalize(), periods=n_windows)
        for w, s in enumerate(starts):
            e = s + span
            base = bt.run(start=s, end=e)
            r_base = daily_log_returns(base.daily_equity)
            for spec in filters:
                for k in range(seeds):
                    seed = 1000 * w + k
                    arm = bt.run(start=s, end=e, filter_fn=make_filter(spec, seed, labels))
                    r_arm = daily_log_returns(arm.daily_equity)
                    d = (r_arm - r_base).dropna().to_numpy()
                    if len(d) < block_days:
                        continue
                    lb = lower_bound(d, alpha=alpha, block=block_days, reps=reps, seed=seed)
                    evaluated = arm.candidates[arm.candidates["status"].isin(["OPENED", "FILTER_VETO"])] if len(arm.candidates) else arm.candidates
                    vetoes = int((evaluated["status"] == "FILTER_VETO").sum()) if len(evaluated) else 0
                    rows.append({
                        "duration_days": dur, "window_start": s, "filter": spec.name,
                        "kind": spec.kind, "seed": seed, "mean_diff": float(d.mean()),
                        "lower_bound": lb, "pass": lb > delta_floor,
                        "base_trades": len(base.trades), "arm_trades": len(arm.trades),
                        "vetoes": vetoes,
                        "veto_rate": vetoes / len(evaluated) if len(evaluated) else np.nan,
                        "final_diff_usdt": float(arm.daily_equity.iloc[-1] - base.daily_equity.iloc[-1]),
                    })
    runs = pd.DataFrame(rows)
    if runs.empty:
        return {"runs": runs, "summary": pd.DataFrame(), "labels": labels_df}
    summary = runs.groupby(["duration_days", "filter", "kind"]).agg(
        pass_rate=("pass", "mean"), mean_diff_log_per_day=("mean_diff", "mean"),
        median_lower_bound=("lower_bound", "median"), base_trades=("base_trades", "mean"),
        vetoes=("vetoes", "mean"), veto_rate=("veto_rate", "mean"),
        final_diff_usdt=("final_diff_usdt", "mean"), runs=("pass", "size"),
    ).reset_index()
    return {"runs": runs, "summary": summary, "labels": labels_df,
            "settings": {"alpha": alpha, "delta_floor": delta_floor, "block_days": block_days,
                         "reps": reps, "n_windows": n_windows, "seeds": seeds,
                         "dev_start": str(dev_start), "dev_end": str(dev_end)}}


def filter_utility(labels: pd.DataFrame, spec: FilterSpec) -> dict:
    """Expected R removed per candidate by a synthetic filter (standalone view)."""
    r = labels["r_multiple"].dropna()
    if r.empty:
        return {}
    if spec.kind == "NULL_RANDOM":
        p = np.full(len(r), spec.veto_prob)
    else:
        p = np.where(r < 0, spec.loser_recall, spec.winner_false_veto)
    return {"veto_rate": float(p.mean()), "r_saved_per_candidate": float((-r * p).mean())}
