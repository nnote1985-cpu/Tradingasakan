"""Daily BTC risk check (path A). Prints JSON then a Thai summary.

    SSL_CERT_FILE=... uv run python scripts/risk_check.py [--end YYYY-MM-DD] [--json-out PATH]

Uses data.binance.vision only; the latest closed UTC day appears there about
8 hours after midnight UTC.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from trading.alerts.live_data import daily_funding, daily_klines, missing_days, open_interest
from trading.alerts.risk_check import render_th, signals, summary

SYMBOL = "BTCUSDT"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="last UTC day to include (default: yesterday)")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    end = date.fromisoformat(args.end) if args.end else datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=400)

    daily = daily_klines(SYMBOL, start, end)
    funding, est = daily_funding(SYMBOL, end - timedelta(days=40), end)
    oi = open_interest(SYMBOL, end - timedelta(days=8), end)
    sig = signals(daily, funding, oi)
    last = sig.index[-1]
    s = summary(sig, funding_estimated=bool(est.loc[est.index > last - timedelta(days=7)].any()))
    s["data_through"] = str(last.date())
    s["stale"] = last.date() < end
    s["missing_days"] = missing_days(daily.index, start, last.date())
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print()
    print(render_th(s))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
