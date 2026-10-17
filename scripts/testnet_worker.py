"""Run the P1 Testnet worker (docs/testnet-development-plan.md §10 P1).

    uv run python scripts/testnet_worker.py            # run until Ctrl+C
    uv run python scripts/testnet_worker.py --once     # start, one tick, stop (a setup check)

The worker sends no orders yet. It holds the writer lease, writes
``var/worker/heartbeat.json``, checks the clock against the Testnet, checks
the Testnet key, pings the external dead-man and sends alerts to Telegram.
Since P2 it also runs the feeds: Mainnet 1m/1d klines into var/ingest/bars,
Testnet BTCUSDT quotes, and the Testnet user stream checked against REST.
Run it under the process manager (scripts/windows/testnet-worker.ps1 on the
PC, deploy/systemd/tradingasakan-worker.service on the VPS) so a crash restarts it.

Environment (all on the Host only; none of them belong in the repo or in chat):
    TRADING_MODE=TESTNET, TESTNET_EXECUTION_ENABLED=true
    DATABASE_URL                            writer lease (required with execution enabled)
    BINANCE_TESTNET_API_KEY + BINANCE_TESTNET_PRIVATE_KEY_PATH (or BINANCE_TESTNET_API_SECRET)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID    alerts (optional; queued in var/worker/alerts.jsonl without them)
    DEADMAN_PING_URL                        external monitor ping, e.g. https://hc-ping.com/<uuid> (optional)
    WORKER_ACCOUNT                          default DEFAULT
    WORKER_FEEDS=off                        run without the P2 feeds (klines, quotes, user stream)
"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading.config import load_settings
from trading.ingest.bars import BarStore
from trading.ingest.service import IngestService
from trading.runtime.alerts import AlertQueue, TelegramSink
from trading.runtime.credentials import CredentialError, load_testnet_credentials
from trading.runtime.deadman import DeadMan
from trading.runtime.worker import LeaseLost, TestnetRest, Worker, WorkerConfig, WorkerRefused

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var" / "worker"

# Exit codes the process manager reads: 0 clean stop, 2 configuration refused (do not restart fast), 3 lease lost.
EXIT_REFUSED, EXIT_LEASE = 2, 3


def _log(msg: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {msg}", flush=True)


def build(env: dict[str, str] | None = None) -> tuple[Worker, Any]:
    env = dict(os.environ) if env is None else env
    settings = load_settings()
    host = platform.node() or "host"
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    sink = TelegramSink(token, chat) if token and chat else None
    alerts = AlertQueue(VAR / "alerts.jsonl", sink, host, lambda: datetime.now(UTC), log=_log)
    creds = load_testnet_credentials(settings, env) if settings.testnet_execution_enabled else None
    ping = env.get("DEADMAN_PING_URL", "").strip()
    conn = None
    store = None
    if settings.database_url:
        import psycopg

        from trading.execution.store import ExecStore

        conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=10)
        store = ExecStore(conn, env.get("WORKER_ACCOUNT", "DEFAULT"))
    cfg = WorkerConfig(owner=f"{host}:{os.getpid()}", heartbeat_path=VAR / "heartbeat.json")
    rest = TestnetRest(creds)
    feeds = None if env.get("WORKER_FEEDS", "on").strip().lower() == "off" else IngestService(BarStore(ROOT / "var" / "ingest" / "bars"), rest)
    worker = Worker(settings, cfg, rest, alerts, deadman=DeadMan(ping) if ping else None, store=store, feeds=feeds, log=_log)
    if sink is None:
        _log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set: alerts stay queued in var/worker/alerts.jsonl")
    if not ping:
        _log("DEADMAN_PING_URL not set: no external dead-man")
    return worker, conn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="start, run one tick, stop")
    args = ap.parse_args(argv)
    try:
        worker, conn = build()
    except (CredentialError, WorkerRefused, ValueError) as e:
        _log(f"refused: {e}")
        return EXIT_REFUSED
    stopping: list[str] = []

    def _stop(signum: int, _frame: object) -> None:
        stopping.append(signal.Signals(signum).name)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        if args.once:
            worker.start()
            worker.tick()
            reason = "--once"
        else:
            worker.run(lambda: bool(stopping))
            reason = stopping[0] if stopping else "loop ended"
        worker.stop(reason)
        st = worker.state
        _log(f"state: safety {st.safety}, lease {st.lease_token}, clock {st.clock_offset_ms} ms ({st.clock_status}), "
             f"signing paused {st.signing_paused}, account ok {st.account_ok}, dead-man ok {st.deadman_ok}")
        return 0
    except LeaseLost as e:
        _log(f"stopped: {e}")
        return EXIT_LEASE
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
