"""Equivalence check for pathA-v1.2 (docs/pathA-v1.2-plan.md, first acceptance test).

    uv run python research/spike/check_pathA_v12_equivalence.py

Drives the pathA-v1.1 float book and the pathA-v1.2 Shadow book with the same
daily pathA weights (from trading.alerts.risk_check on perp closes and funding)
and the same BTCUSDT spot 1h bars, over the 2025 holdout. The holdout is already
spent: this compares two books, it does not test the strategy, and nothing here
changes a rule. Writes /mnt/project-files/pathA-v1.2-checks/equivalence.json.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from common import jsonable

from trading.alerts.risk_check import signals
from trading.market_data.binance_public import _download_zip, _months, load, load_funding, parse_klines_csv
from trading.paper import ledger as v11
from trading.shadow.rebalance_book import PathABook
from trading.strategies.trend_regime_v2 import daily_bars

SPOT = "https://data.binance.vision/data/spot"
START, END = pd.Timestamp("2025-01-24", tz="UTC"), pd.Timestamp("2025-12-31", tz="UTC")
OUT = Path("/mnt/project-files/pathA-v1.2-checks")


def spot_hourly() -> pd.DataFrame:
    raw = Path("data/binance_spot/raw")
    raw.mkdir(parents=True, exist_ok=True)
    frames = []
    for y, m in _months(date(2025, 1, 1), date(2026, 1, 31)):
        inner, info = _download_zip(f"{SPOT}/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{y}-{m:02d}.zip", raw)
        if info.get("status") != "VERIFIED" or inner is None:
            raise RuntimeError(f"{y}-{m:02d}: {info}")
        frames.append(parse_klines_csv(inner))
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="first")]


def main() -> None:
    perp = daily_bars(load(Path("data/binance_um"), "BTCUSDT", "1h"))
    f = load_funding(Path("data/binance_um"), "BTCUSDT")
    w = signals(perp, f.groupby(f.index.floor("1D")).sum())["suggested_btc_weight"]
    hourly = spot_hourly()
    daily = daily_bars(hourly)

    def stamps(day):
        close = day + pd.Timedelta(days=1)
        return close + pd.Timedelta(minutes=5), close + pd.Timedelta(minutes=6)

    def weight(day):
        x = w.get(day)
        return None if x is None or pd.isna(x) else float(x)

    r, dn = stamps(START)
    w0 = weight(START) if weight(START) is not None else 1.0
    old = v11.new_state(str(START.date()), w0, r, dn, ["open"])
    new = PathABook()
    new.open(str(START.date()), w0, r, dn, ["open"])
    days = daily.index[(daily.index > START) & (daily.index <= END)]
    for day in days:
        sub_d, sub_h = daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)]
        v11.advance(old, sub_d, sub_h)
        new.advance(sub_d, sub_h)
        r, dn = stamps(day)
        v11.decide(old, str(day.date()), weight(day), r, dn, ["x"])
        new.decide(str(day.date()), weight(day), r, dn, ["x"])

    first_halt = next((e["decided_on"] for e in new.journal if e["kind"] == "halt"), None)
    rows = [(a["date"], a["equity"], float(b["equity"])) for a, b in zip(old["history"], new.history)
            if first_halt is None or a["date"] <= first_halt]
    rel = max(abs(nb / oa - 1) for _, oa, nb in rows)
    to = [t for t in old["trades"]]
    tn = [t for t in new.trades]
    same_trades = len(to) == len(tn) and all(
        (a["book"], a["at"], a["side"]) == (b["book"], b["at"], b["side"])
        and abs(float(b["qty"]) / a["qty"] - 1) < 1e-9 for a, b in zip(to, tn))
    report = {"period": [str(START.date()), str(END.date())], "days": len(rows),
              "trades_v11": len(to), "trades_v12": len(tn), "same_trades": same_trades,
              "max_rel_equity_diff": rel, "pass_1e-6": bool(rel <= 1e-6 and same_trades),
              "v11": v11.performance(old), "v12": new.performance(),
              "alerts_v12": [{k: a[k] for k in ("time", "alert", "trading_equity")} for a in new.risk.alerts],
              "first_halt_v12": first_halt}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "equivalence.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
