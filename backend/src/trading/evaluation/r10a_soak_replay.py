"""Replay the tn-r10a-code-v1 Testnet soak through v1.1 and v2 (docs/m3-strategy-core-plan.md §4.1).

For each stored decision the inputs are rebuilt as the worker saw them that day:

- daily perp klines up to the day (400 days); before 2026-11-11 the archive had no 2026-10-28, so that
  row is dropped, and for v1.1 rows (which fill archive gaps from REST) it comes back as a filled day;
- daily funding: the 7 days of the stored snapshot (actual print or premium-index proxy, as recorded),
  with the earlier days of the 40-day window taken from a later archive download.

Then the v1.1 rule (``trading.runtime.r10a.decide``) and the v2 strategy (``R10aV2.decide``) decide on
those same inputs and are compared with each other and with the stored row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from trading.contracts.strategy import StrategyInputs, TargetDecision
from trading.strategies.r10a_v2 import FUNDING_DAYS, HISTORY_DAYS, R10aV2

GAP_DAY = pd.Timestamp("2026-10-28", tz="UTC")
GAP_PUBLISHED = date(2026, 11, 11)          # data.binance.vision added 2026-10-28 on 2026-11-11 (before that day's decision)
READY_AT = datetime(2026, 11, 15, tzinfo=UTC)


@dataclass(frozen=True)
class Day:
    day: date
    stored: Mapping[str, Any]
    v11: Any                                # trading.runtime.r10a.Decision | None
    v2: TargetDecision | None

    @property
    def same(self) -> bool:
        """v1.1 and v2 agree: both wait, or the same weight, reason, snapshot and hash."""
        if self.v11 is None or self.v2 is None:
            return self.v11 is None and self.v2 is None
        return (self.v11.weight == self.v2.target_weight and self.v11.notes == self.v2.reason
                and self.v11.inputs == dict(self.v2.inputs) and self.v11.inputs_sha256 == self.v2.inputs_sha256)

    @property
    def matches_stored(self) -> bool:
        return self.v2 is not None and self.v2.inputs_sha256 == self.stored["inputs_sha256"]


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d, tz="UTC")


def rebuild(row: Mapping[str, Any], klines: pd.DataFrame, funding_hist: pd.Series) -> StrategyInputs:
    """The inputs the worker had for the stored decision ``row``."""
    day = date.fromisoformat(row["day"])
    daily = klines[(klines.index >= _ts(day - timedelta(days=HISTORY_DAYS))) & (klines.index <= _ts(day))]
    filled: list[str] = []
    if day < GAP_PUBLISHED:
        daily = daily[daily.index != GAP_DAY]
        if str(row["rules_version"]).endswith("v1.1"):
            from trading.runtime.r10a import fill_gaps

            def rest(a: date, b: date) -> pd.DataFrame:
                return klines[(klines.index >= _ts(a)) & (klines.index <= _ts(b))]

            daily, filled = fill_gaps(daily, day, rest)
    window = row["inputs"]["funding_window"]
    seen = [x for x in window if x["source"] != "missing"]
    f = pd.Series({_ts(date.fromisoformat(x["day"])): x["funding"] for x in seen}, dtype=float)
    est = pd.Series({_ts(date.fromisoformat(x["day"])): x["source"] == "proxy" for x in seen}, dtype=bool)
    start = _ts(date.fromisoformat(window[0]["day"]))
    pre = funding_hist[(funding_hist.index < start) & (funding_hist.index >= _ts(day - timedelta(days=FUNDING_DAYS)))]
    funding = pd.concat([pre.astype(float), f]).sort_index()
    flags = pd.concat([pd.Series(False, index=pre.index, dtype=bool), est]).sort_index()
    return StrategyInputs(bars=daily, series={"funding": funding, "funding_est": flags}, extra={"rest_filled_days": filled})


def replay(rows: list[Mapping[str, Any]], klines: pd.DataFrame, funding_hist: pd.Series) -> list[Day]:
    from trading.runtime.r10a import decide

    v2 = R10aV2()
    out = []
    for row in rows:
        day = date.fromisoformat(row["day"])
        inputs = rebuild(row, klines, funding_hist)
        filled = list(inputs.extra["rest_filled_days"])
        a = decide(inputs.bars, inputs.series["funding"], inputs.series["funding_est"], day, READY_AT, filled or None)
        b = v2.decide(inputs, day, READY_AT)
        out.append(Day(day, row, a, b))
    return out


def load_fixture(directory: str) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.Series]:
    import json
    from pathlib import Path

    p = Path(directory)
    rows = json.loads((p / "decisions.json").read_text())
    k = pd.read_csv(p / "klines_1d.csv", index_col=0, float_precision="round_trip")
    k.index = pd.DatetimeIndex(pd.to_datetime(k.index, utc=True), name="open_time")
    f = pd.read_csv(p / "funding_1d.csv", index_col=0, float_precision="round_trip")
    f.index = pd.DatetimeIndex(pd.to_datetime(f.index, utc=True))
    return rows, k, f["funding"].astype(float)
