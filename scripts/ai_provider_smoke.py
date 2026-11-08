"""One real r2 proposal on today's inputs, printed and not stored (Testnet P6 gate, testnet-p6-plan.md §5).

Run on the VM as the service user, with the AI account's env file loaded, before WORKER_AI_R2=on:

    sudo -u trading bash -c 'set -a; . /etc/tradingasakan/worker-AI.env; set +a; \
        cd /opt/tradingasakan && /usr/local/bin/uv run python scripts/ai_provider_smoke.py'

It makes one paid call (about 0.02 USD) with the locked model, prompt and schema, and prints the outcome, tokens,
cost and latency. The key is read from TN_ANTHROPIC_KEY and never printed. Nothing is written to Supabase and no
order is sent, so the call does not count against the book's monthly cap.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading.analysis import pathA_proposer as pp
from trading.analysis.pathA_filter import AnthropicProvider
from trading.runtime.ai_r2 import KEY_ENV, propose_decision


def main() -> int:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"{KEY_ENV} is not set: load the AI account's env file first", file=sys.stderr)
        return 2
    graph = pp.load_proposer_graph()
    provider = AnthropicProvider(graph.role("proposer").model_id, key)
    day = datetime.now(UTC).date() - timedelta(days=1)
    for d in (day, day - timedelta(days=1)):              # today's archive file may not be out yet
        t0 = time.monotonic()
        dec = propose_decision(d, None, 0.0, Decimal("5"), provider, graph)
        if dec is None:
            print(f"{d}: inputs not out yet, trying the day before")
            continue
        rec = dec.inputs.get("proposal")
        if rec is None:
            print(f"{d}: inputs incomplete ({dec.notes}); no call made")
            return 1
        att = rec["attempt"] or {}
        print(f"day {d}  model {att.get('model_id')}  status {att.get('status')}  error {rec['outcome']['error_category']}")
        print(f"target {rec['outcome']['target_weight']}  reason_code {rec['outcome']['reason_code']}  R10a {dec.inputs['code_weight']}")
        print(f"tokens in/out {att.get('input_tokens')}/{att.get('output_tokens')}  cost {rec['cost_usd']} USD  "
              f"{time.monotonic() - t0:.1f} s")
        print(f"notes: {dec.notes}")
        return 0 if rec["outcome"]["accepted"] else 1
    print("no day with inputs yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
