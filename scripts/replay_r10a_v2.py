"""Replay the tn-r10a-code-v1 Testnet soak through v1.1 and tn-r10a-code-v2 (docs/m3-strategy-core-plan.md §4.1).

    uv run python scripts/replay_r10a_v2.py                          # the committed fixture
    uv run python scripts/replay_r10a_v2.py --decisions export.json --download

``export.json`` is the list of ``trading.exec_strategy_decisions`` rows of book ``tn-r10a-code-v1``
(day, rules_version, weight, action, notes, inputs, inputs_sha256, outcome), e.g. from
``select json_agg(... order by day) ...`` in Supabase. ``--download`` takes klines and funding from
data.binance.vision now instead of the fixture. Exit code 1 when any day differs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from trading.evaluation.r10a_soak_replay import load_fixture, replay  # noqa: E402

WAITS = {date(2026, 10, 29)}               # v1 recorded NO_DECISION; v1.1 waits for the day's funding


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", default=str(ROOT / "tests" / "fixtures" / "r10a_soak"))
    ap.add_argument("--decisions", help="JSON export of the stored decisions (default: the fixture's)")
    ap.add_argument("--download", action="store_true", help="klines and funding from data.binance.vision now")
    a = ap.parse_args()
    rows, klines, funding = load_fixture(a.fixture)
    if a.decisions:
        rows = json.loads(Path(a.decisions).read_text())
    if a.download:
        from trading.alerts.live_data import daily_funding, daily_klines

        last = max(date.fromisoformat(r["day"]) for r in rows)
        first = min(date.fromisoformat(r["day"]) for r in rows)
        klines = daily_klines("BTCUSDT", first - timedelta(days=400), last)
        funding, _ = daily_funding("BTCUSDT", first - timedelta(days=40), last)
    bad = 0
    print(f"{'day':10}  {'stored':12} {'weight':>6}  v1.1=v2  v2=stored")
    for d in replay(rows, klines, funding):
        ok = d.same and (d.matches_stored or (d.day in WAITS and d.v2 is None))
        bad += not ok
        w = "wait" if d.v2 is None else ("-" if d.v2.target_weight is None else f"{d.v2.target_weight:g}")
        print(f"{d.day}  {d.stored['action']:12} {w:>6}  {'yes' if d.same else 'NO':7}  "
              f"{'yes' if d.matches_stored else ('wait' if d.day in WAITS and d.v2 is None else 'NO')}")
    print(f"{len(rows) - bad}/{len(rows)} days as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
