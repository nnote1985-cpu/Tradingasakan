"""Print idempotent SQL that records frozen paper books in the `trading` schema (§20).

    uv run python scripts/trading_sync_sql.py [--root /mnt/project-files/paper-trading] > sync.sql

Each rules version becomes a strategy_versions row and two SHADOW portfolios
(`<version>/paper` and its `<version>/buy_and_hold` benchmark). Fills become
balanced USDT journal entries; daily marks become equity snapshots. Keys are
deterministic, so re-running inserts nothing new. Needs trading.workspace_settings
(the owner) to exist first.
"""

from __future__ import annotations

import argparse
import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path("/mnt/project-files/paper-trading")
FEE = Decimal("0.001")                   # trend_regime_v2.RegimeCosts.fee used by the paper ledger
Q = Decimal("0.000000000001")


def q(v) -> str:
    return "null" if v is None else "'" + str(v).replace("'", "''") + "'"


def d(x) -> Decimal:
    return Decimal(str(x)).quantize(Q, rounding=ROUND_HALF_EVEN)


def _entry(key: str, portfolio: str, at: str, memo: str, lines: list[tuple[str, Decimal]]) -> list[str]:
    total = sum(a for _, a in lines)
    if total != 0:                                   # absorb rounding so every entry balances exactly
        lines = lines[:-1] + [(lines[-1][0], lines[-1][1] - total)]
    vals = ", ".join(f"((select id from e), {i}, {q(acct)}, {q(a)}::numeric)" for i, (acct, a) in enumerate(lines, 1) if a != 0)
    return [f"with e as (insert into trading.journal_entries (portfolio_id, source_event_key, currency, occurred_at, memo) "
            f"select id, {q(key)}, 'USDT', {q(at)}, {q(memo)} from trading.portfolios where label = {q(portfolio)} "
            f"on conflict (source_event_key) do nothing returning id) "
            f"insert into trading.journal_lines (entry_id, line_no, ledger_account, amount) select * from (values {vals}) v "
            f"where exists (select 1 from e);"]


def book_sql(state: dict, commit: str) -> list[str]:
    v = state["rules_version"]
    out = [f"insert into trading.strategy_versions (label, code_commit, frozen_ref) values ({q(v)}, {q(commit)}, {q('frozen/' + v)}) "
           "on conflict (label) do nothing;"]
    opened_at = state["journal"][0].get("at") or state["journal"][0]["date"]
    for book in ("paper", "buy_and_hold"):
        label = f"{v}/{book}"
        out.append("insert into trading.portfolios (label, owner_id, mode, currency, initial_equity, active_version_id) "
                   f"select {q(label)}, w.owner_id, 'SHADOW', 'USDT', {q(d(state['start_equity']))}, "
                   f"(select id from trading.strategy_versions where label = {q(v)}) from trading.workspace_settings w "
                   "on conflict (label) do nothing;")
        out += _entry(f"{label}:funding", label, opened_at, "starting cash",
                      [("cash", d(state["start_equity"])), ("owner_capital", -d(state["start_equity"]))])
        qty, cost = Decimal(0), Decimal(0)
        for t in (t for t in state["trades"] if t["book"] == book):
            if t["side"] == "BUY":
                spend = d(t["value"])
                fee = d(spend * FEE)
                lines = [("cash", -spend), ("btc_position_cost", spend - fee), ("fees", fee)]
                qty += Decimal(str(t["qty"]))
                cost += spend - fee
            else:
                sold = Decimal(str(t["qty"]))
                gross = d(sold * Decimal(str(t["price"])))
                fee = d(gross * FEE)
                removed = d(cost * sold / qty) if qty else Decimal(0)
                lines = [("cash", d(t["value"])), ("fees", fee), ("btc_position_cost", -removed),
                         ("realized_pnl", -(gross - removed))]
                qty -= sold
                cost -= removed
            out += _entry(f"{label}:fill:{t['at']}", label, t["at"], f"{t['side']} {t['qty']} BTC @ {t['price']}", lines)
        for h in state["history"]:
            eq = d(h[book if book == "buy_and_hold" else "equity"])
            at = f"{h['date']}T23:59:59.999+00:00"
            exposure = d(eq * Decimal(str(h["weight_held_at_close"]))) if book == "paper" else None
            out.append("insert into trading.equity_snapshots (portfolio_id, at, trading_equity, economic_equity, mark_price, exposure) "
                       f"select id, {q(at)}, {q(eq)}, {q(eq)}, {q(d(h['close']))}, {q(exposure)} from trading.portfolios "
                       f"where label = {q(label)} on conflict do nothing;")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--commit", action="append", default=["pathA-v1=42920ba"],
                    help="version=commit of each frozen branch")
    args = ap.parse_args()
    commits = dict(c.split("=", 1) for c in args.commit)
    lines = ["begin;", "do $$ begin if not exists (select 1 from trading.workspace_settings) then "
             "raise exception 'trading.workspace_settings is empty: set the owner first'; end if; end $$;"]
    for p in sorted(Path(args.root).glob("*/state.json")):
        s = json.loads(p.read_text())
        lines += book_sql(s, commits[s["rules_version"]])
    lines.append("commit;")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
