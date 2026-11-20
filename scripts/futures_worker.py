"""Run the futures desk worker: sync of the Binance Futures Demo account (docs/futures-desk-plan.md §5)
and, with FUTURES_ORDERS_ENABLED=true, the manual order tickets queued from the web page (§7).

    uv run python scripts/futures_worker.py            # run until Ctrl+C / SIGTERM
    uv run python scripts/futures_worker.py --once     # start, one sync, stop (a setup check)

Environment (Host only; never in the repo or in chat):
    DATABASE_URL                                     required: the worker writes trading.fut_* and holds a writer lease
    FUTURES_DEMO_API_KEY + FUTURES_DEMO_API_SECRET   a Demo Trading key from demo.binance.com (or
                         FUTURES_DEMO_PRIVATE_KEY_PATH for an Ed25519 key). Read permission is enough for stage 1.
    FUTURES_ACCOUNT                                  name shown on the page, default FUT1
    FUTURES_ORDERS_ENABLED=true                      send queued tickets (default off: read-only). The key then
                                                     needs trade permission on demo.binance.com.
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID             alerts (optional)
Mainnet key variables (BINANCE_API_KEY, ...) must not be set: the worker refuses to start if they are.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading.config import load_settings
from trading.futures.executor import Executor
from trading.futures.rest import FuturesDemoRest, load_demo_credentials
from trading.futures.store import FuturesStore
from trading.futures.worker import Config, FuturesWorker, LeaseLost
from trading.runtime.alerts import AlertQueue, TelegramSink
from trading.runtime.credentials import CredentialError

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
EXIT_REFUSED, EXIT_LEASE = 2, 3


def _log(msg: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="start, one sync, stop")
    args = ap.parse_args(argv)
    env = os.environ
    settings = load_settings()
    account = env.get("FUTURES_ACCOUNT", "FUT1").strip() or "FUT1"
    try:
        if not ACCOUNT_NAME.fullmatch(account):
            raise ValueError(f"FUTURES_ACCOUNT must be 1-32 letters, digits, '_' or '-', got {account!r}")
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required")
        creds = load_demo_credentials(env)
    except (CredentialError, ValueError) as e:
        _log(f"refused: {e}")
        return EXIT_REFUSED
    import psycopg

    host = platform.node() or "host"
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    alerts = AlertQueue(ROOT / "var" / f"futures-{account}" / "alerts.jsonl", TelegramSink(token, chat) if token and chat else None,
                        f"{host} (Futures {account})", lambda: datetime.now(UTC), log=_log)
    orders = env.get("FUTURES_ORDERS_ENABLED", "false").strip().lower() == "true"
    conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=10)
    rest = FuturesDemoRest(creds, orders_enabled=orders)
    store = FuturesStore(conn, account)
    worker = FuturesWorker(rest, store, alerts, Config(owner=f"{host}:{os.getpid()}", host=host),
                           executor=Executor(rest, store, alerts, log=_log) if orders else None, log=_log)
    stopping: list[str] = []
    signal.signal(signal.SIGINT, lambda s, _f: stopping.append(signal.Signals(s).name))
    signal.signal(signal.SIGTERM, lambda s, _f: stopping.append(signal.Signals(s).name))
    _log(f"futures worker {account}: Futures Demo, key {creds!r}, orders {'ON' if orders else 'off (read-only)'}")
    try:
        if args.once:
            worker.start()
            worker.sync()
            st = worker.state
            _log(f"one sync done: fills +{st.fills_added}, income +{st.income_added}, clock {st.clock_offset_ms} ms")
        else:
            worker.run(lambda: bool(stopping))
        return 0
    except LeaseLost as e:
        _log(f"stopped: {e}")
        return EXIT_LEASE
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
