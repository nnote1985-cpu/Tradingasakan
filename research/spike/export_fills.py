"""Copy a backtest's trade legs (run_*.py trades_<set>.csv) into its web results file, so the
Futures tab can list trades one by one (round trips are paired by apps/api/api/backtests.py).

    python research/spike/export_fills.py /mnt/project-files/r7-results/trades_development.csv research/results/r7-development.json
"""

from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

SIDE = {"1": "long", "-1": "short"}


def fills(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"day": r["time"][:10], "action": r["action"], "side": SIDE[r["side"]],
             "price": str(Decimal(r["price"]).quantize(Decimal("0.01"))),
             "qty": str(Decimal(r["qty"]).quantize(Decimal("0.00000001"))),
             "equity": str(Decimal(r["equity"]).quantize(Decimal("0.01")))} for r in rows]


def main(trades_csv: str, results_json: str) -> None:
    with open(trades_csv, newline="", encoding="utf-8") as f:
        legs = fills(list(csv.DictReader(f)))
    p = Path(results_json)
    r = json.loads(p.read_text(encoding="utf-8"))
    points = r.pop("points")
    r["fills"] = legs
    r["points"] = points
    p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(legs)} legs -> {p}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
