"""Download BTCUSDT USDⓈ-M klines + funding from data.binance.vision.

    uv run python research/spike/download_data.py [--config configs/spike.json]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from trading.market_data.binance_public import download


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/spike.json")
    ap.add_argument("--intervals", nargs="+", default=["1h", "1m"])
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    manifest = download(cfg["symbol"], date.fromisoformat(cfg["data_start"]),
                        date.fromisoformat(cfg["data_end"]), Path(cfg["data_dir"]),
                        intervals=tuple(args.intervals))
    missing = [f["file"] for f in manifest["files"] if f["status"] == "MISSING"]
    print(json.dumps({k: v for k, v in manifest.items() if k.startswith("coverage")}, indent=2))
    print(f"files: {len(manifest['files'])}, missing: {len(missing)}")
    for name in missing:
        print("  MISSING", name)


if __name__ == "__main__":
    main()
