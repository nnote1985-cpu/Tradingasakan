"""Record exactly what a frozen paper book runs on: code, config and data hashes.

Run inside the frozen worktree so the imports are the book's own code:

    cd /home/user/frozen/pathA-v1
    SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python /home/user/tradingasakan/scripts/paper_manifest.py \
        --end 2026-10-14 --state /mnt/project-files/paper-trading/pathA-v1/state.json > manifest.json

Code: commit, tree, and sha256 of every tracked file the book can import, plus the lock
file. Config: the rule constants and costs as loaded. Data: every archive file the book's
own fetch code pulls for the run ending on --end (perp signal files and, from pathA-v1.1,
the spot price files), with the sha256 already checked against Binance's published
.CHECKSUM. Each forward run also records the files it actually fetched in runs.jsonl. Read-only: nothing here writes a book or its state.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def code_section() -> dict:
    files = [f for f in git("ls-files", "backend/src/trading", "scripts/paper_trade.py", "pyproject.toml",
                            "uv.lock", ".python-version").splitlines() if not f.endswith("/")]
    hashes = {f: sha256(Path(f).read_bytes()) for f in sorted(files)}
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "files": hashes, "files_digest": sha256(json.dumps(hashes, sort_keys=True).encode()),
            "python": sys.version.split()[0]}


def config_section() -> dict:
    from trading.alerts import live_data, risk_check
    from trading.paper import ledger
    from trading.strategies.trend_regime_v2 import RegimeCosts

    consts = {k: v for k, v in vars(risk_check).items() if k.isupper() and isinstance(v, (int, float, str))}
    cfg = {"rules_version": ledger.RULES_VERSION, "ledger_format": ledger.VERSION, "start_equity": ledger.START_EQUITY,
           "costs": dataclasses.asdict(RegimeCosts()), "signal_constants": consts,
           "funding_policy": getattr(live_data, "FUNDING_POLICY", None)}
    return {**cfg, "digest": sha256(json.dumps(cfg, sort_keys=True).encode())}


def data_section(end: date, fill_from: date | None) -> dict:
    from trading.alerts import live_data

    seen: dict[str, str] = {}
    original = live_data._get_verified

    def recording(url: str) -> bytes | None:
        blob = original(url)                 # raises if the published checksum does not match
        if blob is not None:
            seen[url] = sha256(blob)
        return blob

    live_data._get_verified = recording      # type: ignore[assignment]
    live_data.daily_klines("BTCUSDT", end - timedelta(days=400), end)
    live_data.daily_funding("BTCUSDT", end - timedelta(days=40), end)
    spot = hasattr(live_data, "spot_daily_klines")          # pathA-v1.1 and later price on spot bars
    if spot:
        live_data.spot_daily_klines("BTCUSDT", (fill_from or end) - timedelta(days=1), end)
    if fill_from is not None and fill_from <= end:
        (live_data.spot_hourly_klines if spot else live_data.hourly_klines)("BTCUSDT", fill_from, end)
    live_data._get_verified = original       # type: ignore[assignment]
    files = dict(sorted(seen.items()))
    return {"end": str(end), "signals_source": live_data.BASE,
            "price_source": getattr(live_data, "SPOT_BASE", live_data.BASE), "verified_against": "published .CHECKSUM (sha256)",
            "files": files, "digest": sha256(json.dumps(files, sort_keys=True).encode())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", required=True, help="last closed UTC day the run used")
    ap.add_argument("--fill-from", help="first day of hourly bars fetched for fills (default: none)")
    ap.add_argument("--state", help="state.json to hash (read only)")
    args = ap.parse_args()
    out = {"manifest_version": 1, "code": code_section(), "config": config_section(),
           "data": data_section(date.fromisoformat(args.end),
                                date.fromisoformat(args.fill_from) if args.fill_from else None)}
    if args.state:
        out["state"] = {"path": args.state, "sha256": sha256(Path(args.state).read_bytes())}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
