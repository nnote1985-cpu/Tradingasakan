"""Print idempotent SQL that records frozen paper books in the `trading` schema (§20).

    uv run python scripts/trading_sync_sql.py [--root /mnt/project-files/paper-trading] > sync.sql

Each rules version becomes a strategy_versions row and two SHADOW portfolios
(`<version>/paper` and its `<version>/buy_and_hold` benchmark). Fills become
balanced USDT journal entries; daily marks become equity snapshots. Keys are
deterministic, so re-running inserts nothing new. Needs trading.workspace_settings
(the owner) to exist first.

Books in the pathA-v1 float format (pathA-v1, pathA-v1.1) are rebuilt from their
trade list. pathA-v1.2 (format rebalance_book.v1) already keeps a balanced Decimal
spot journal, so its entries are copied line for line, keyed
`<version>/<book>:<ledger key>`, with the account names mapped to the ones used
here (capital -> owner_capital, inventory_at_cost -> btc_position_cost). Its spot.v1 alerts,
HALTs and resumes go to trading.risk_events.

The Phase 5 paired run (format paired_book.v1, docs/phase-5-paired-plan.md) becomes the portfolios
`pathA-p5-r1/code`, `pathA-p5-r1/ai` and `pathA-p5-r1/buy_and_hold` (the Code arm's benchmark;
the AI arm's is the same), each copied like a pathA-v1.2 book, and every question to the AI
becomes a trading.agent_decisions row on the AI portfolio.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
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


ACCOUNTS = {"capital": "owner_capital", "inventory_at_cost": "btc_position_cost"}
COMMITS = ["pathA-v1=42920ba", "pathA-v1.1=5306f62", "pathA-v1.2=34028a6", "pathA-p5-r1=2ae53c7"]   # frozen/<version> heads


def _version_and_portfolio(v: str, commit: str, label: str, start_equity) -> list[str]:
    return [f"insert into trading.strategy_versions (label, code_commit, frozen_ref) values ({q(v)}, {q(commit)}, "
            f"{q('frozen/' + v)}) on conflict (label) do nothing;",
            "insert into trading.portfolios (label, owner_id, mode, currency, initial_equity, active_version_id) "
            f"select {q(label)}, w.owner_id, 'SHADOW', 'USDT', {q(d(start_equity))}, "
            f"(select id from trading.strategy_versions where label = {q(v)}) from trading.workspace_settings w "
            "on conflict (label) do nothing;"]


def _spot_book_sql(v: str, commit: str, state: dict, label: str, bh_label: str | None) -> list[str]:
    """One spot book (PathABook state): copy each SpotLedger entry as it is; never re-derive amounts."""
    out: list[str] = []
    books = [("paper", "book", label)] + ([("buy_and_hold", "buy_and_hold", bh_label)] if bh_label else [])
    for book, key, lab in books:
        out += _version_and_portfolio(v, commit, lab, state["start_equity"])
        for e in state[key]["entries"]:
            lines = [(ACCOUNTS.get(a, a), Decimal(x)) for a, x in e["lines"]]
            if sum(x for _, x in lines) != 0:
                raise SystemExit(f"{lab} entry {e['key']} does not balance: the state is not a valid book")
            dd = e["data"]
            memo = "starting cash" if e["kind"] == "open" else f"{dd.get('side')} {dd.get('qty')} BTC @ {dd.get('price')}"
            if e["kind"] == "operating_cost":
                memo = dd.get("memo") or "operating cost"
            out += _entry(f"{lab}:{e['key']}", lab, e["at"], memo, lines)
        for h in state["history"]:
            at = f"{h['date']}T23:59:59.999+00:00"
            tr = d(h["equity"] if book == "paper" else h["buy_and_hold"])
            ec = d(h["economic_equity"] if book == "paper" else h["buy_and_hold"])
            exposure = d(tr * Decimal(str(h["weight_held_at_close"]))) if book == "paper" else None
            out.append("insert into trading.equity_snapshots (portfolio_id, at, trading_equity, economic_equity, mark_price, exposure) "
                       f"select id, {q(at)}, {q(tr)}, {q(ec)}, {q(d(h['close']))}, {q(exposure)} from trading.portfolios "
                       f"where label = {q(lab)} on conflict do nothing;")
    out += _risk_events_sql(state.get("journal", []), label)
    return out


def _risk_events_sql(journal: list[dict], label: str) -> list[str]:
    out = []
    for e in journal:
        if e["kind"] == "alert":
            at, key, reason, eq, hwm = e["time"], f"alert:{e['time']}:{e['alert']}", e["alert"], e.get("trading_equity"), e.get("high_water_mark")
        elif e["kind"] == "halt":
            at, key, reason, eq, hwm = e["decided_at"], f"halt:{e['decided_on']}", e["reason"], e.get("trading_equity"), e.get("high_water_mark")
        elif e["kind"] == "resume":
            at, key, reason, eq, hwm = e["at"], f"resume:{e['at']}", f"{e['by']}: {e['reason']}", e.get("equity"), None
        else:
            continue
        out.append("insert into trading.risk_events (portfolio_id, source_event_key, kind, at, reason, trading_equity, high_water_mark) "
                   f"select id, {q(label + ':' + key)}, {q(e['kind'])}, {q(at)}, {q(reason)}, "
                   f"{q(None if eq is None else d(eq))}, {q(None if hwm is None else d(hwm))} from trading.portfolios "
                   f"where label = {q(label)} on conflict (source_event_key) do nothing;")
    return out


def rebalance_book_sql(state: dict, commit: str) -> list[str]:
    """pathA-v1.2: the book and its buy & hold benchmark."""
    v = state["rules_version"]
    return _spot_book_sql(v, commit, state, f"{v}/paper", f"{v}/buy_and_hold")


def _ms(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return max(0, round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() * 1000))


def paired_book_sql(state: dict, commit: str) -> list[str]:
    """Phase 5: both arms, the Code arm's benchmark, and every AI question on the AI portfolio."""
    v = state["experiment_id"]
    out = _spot_book_sql(v, commit, state["code"], f"{v}/code", f"{v}/buy_and_hold")
    out += _spot_book_sql(v, commit, state["ai"], f"{v}/ai", None)
    for r in state["filter_log"] + state["diagnostics"]:
        a, dec, resp = r["attempt"], r["decision"], r.get("response") or {}
        cols = {"proposal_id": r["proposal_id"], "kind": r["kind"], "decided_on": r["proposal_id"].split(":")[1],
                "current_weight": r["current"], "target_weight": r["target"], "decision": dec["decision"],
                "reason_code": dec["reason_code"], "reason": resp.get("reason"), "attempt_status": a["status"],
                "error_category": a.get("error_category"), "model_id": a.get("model_id"),
                "policy_version": dec.get("policy_version"), "input_tokens": a.get("input_tokens"),
                "output_tokens": a.get("output_tokens"), "cost_usd": r["cost_usd"],
                "latency_ms": _ms(a.get("request_at"), a.get("response_at")), "decided_at": r["decided_at"]}
        out.append(f"insert into trading.agent_decisions (portfolio_id, {', '.join(cols)}) "
                   f"select id, {', '.join(q(x) for x in cols.values())} from trading.portfolios "
                   f"where label = {q(v + '/ai')} on conflict (proposal_id) do nothing;")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--commit", action="append", default=list(COMMITS),
                    help="version=commit of each frozen branch (later values win)")
    args = ap.parse_args()
    commits = dict(c.split("=", 1) for c in args.commit)
    lines = ["begin;", "do $$ begin if not exists (select 1 from trading.workspace_settings) then "
             "raise exception 'trading.workspace_settings is empty: set the owner first'; end if; end $$;"]
    for p in sorted(Path(args.root).glob("*/state.json")):
        s = json.loads(p.read_text())
        v = s.get("rules_version") or s["experiment_id"]
        if v not in commits:
            raise SystemExit(f"no frozen commit known for {v}: pass --commit {v}=<sha>")
        if s.get("format") == "paired_book.v1":
            lines += paired_book_sql(s, commits[v])
        elif s.get("format") == "rebalance_book.v1":
            lines += rebalance_book_sql(s, commits[v])
        else:
            lines += book_sql(s, commits[v])
    lines.append("commit;")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
