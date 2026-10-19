"""Ask the Testnet worker to HALT or RESUME (docs/testnet-development-plan.md §8.5, P5).

    uv run python scripts/testnet_request.py halt --actor note --reason "HALT drill"
    uv run python scripts/testnet_request.py resume --actor note --reason "drill done"
    uv run python scripts/testnet_request.py status

The request goes into trading.exec_operator_requests; the worker that holds the
writer lease carries it out on its next tick (about 2 s) and writes the outcome
back. HALT blocks entries, raises the safety epoch, cancels open orders and sells
the guarded books; the account is HALTED once they are flat. RESUME works only
from HALTED and goes through RECOVERING, so entries start again only after the
account check is MATCH. Nothing here talks to Binance.

Needs DATABASE_URL (and WORKER_ACCOUNT if not DEFAULT) on the Host, like the worker.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from trading.config import load_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=["halt", "resume", "status"])
    ap.add_argument("--actor", default="", help="who asks (required for halt and resume)")
    ap.add_argument("--reason", default="", help="why (required for halt and resume)")
    ap.add_argument("--wait", type=float, default=30.0, help="seconds to wait for the worker's answer (0: do not wait)")
    args = ap.parse_args(argv)
    settings = load_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    import psycopg

    from trading.execution.store import ExecStore

    store = ExecStore(psycopg.connect(settings.database_url, autocommit=True, connect_timeout=10),
                      os.environ.get("WORKER_ACCOUNT", "DEFAULT"))
    safety = store.load_safety()
    print(f"account {store.account}: {safety.state.value}, epoch {safety.epoch}" +
          (f", halt reason: {safety.halt_reason}" if safety.halt_reason else ""))
    if args.kind == "status":
        return 0
    if not args.actor.strip() or not args.reason.strip():
        print("halt and resume need --actor and --reason", file=sys.stderr)
        return 2
    rid = store.request(args.kind.upper(), args.actor.strip(), args.reason.strip())
    print(f"request {rid} queued: {args.kind.upper()} by {args.actor.strip()}")
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        row = store.conn.execute("select outcome from trading.exec_operator_requests where id = %s", (rid,)).fetchone()
        if row and row[0] is not None:
            print(f"worker: {row[0]}")
            return 0
        time.sleep(1)
    if args.wait > 0:
        print("no answer yet: is the worker running and holding the lease? The request stays queued until it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
