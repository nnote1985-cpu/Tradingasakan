"""Apply every Supabase migration to a throwaway local PostgreSQL and check grants and invariants.

Skipped when PostgreSQL server binaries are not installed (set PG_BIN to their directory).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[2]


def _pg_bin() -> Path | None:
    cands = [os.environ.get("PG_BIN", "")] + sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)
    for c in cands:
        if c and (Path(c) / "initdb").exists():
            return Path(c)
    return None


@pytest.fixture(scope="module")
def db():
    pg = _pg_bin()
    if pg is None:
        pytest.skip("no PostgreSQL server binaries")
    tmp = Path(tempfile.mkdtemp())
    data, sock = tmp / "data", tmp / "sock"
    sock.mkdir()
    run = dict(check=True, capture_output=True)
    user = "postgres"
    if os.geteuid() == 0:                       # initdb refuses to run as root
        shutil.chown(tmp, "nobody")
        shutil.chown(sock, "nobody")
        pre = ["runuser", "-u", "nobody", "--"]
        user = "nobody"
    else:
        pre = []
    subprocess.run(pre + [str(pg / "initdb"), "-D", str(data), "-U", user, "-A", "trust"], timeout=120, **run)
    # -l keeps the server's output off our pipes; otherwise subprocess waits on the daemon forever
    subprocess.run(pre + [str(pg / "pg_ctl"), "-D", str(data), "-l", str(tmp / "pg.log"),
                          "-o", f"-k {sock} -c listen_addresses=''", "-w", "-t", "60", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    try:
        conn = psycopg.connect(host=str(sock), user=user, dbname="postgres", autocommit=True)
        conn.execute((ROOT / "supabase/tests/bootstrap_local.sql").read_text())
        for m in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            conn.execute(m.read_text())
        yield conn
        conn.close()
    finally:
        subprocess.run(pre + [str(pg / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(tmp, ignore_errors=True)


def _as(conn, role: str, sql: str):
    with conn.transaction():
        conn.execute(f"set local role {role}")
        return conn.execute(sql).fetchall()


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_browser_roles_cannot_read_trading_or_paper(db, role):
    for table in ("trading.portfolios", "trading.operator_roles", "paper.books"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(db, role, f"select * from {table}")


def test_rls_enabled_on_every_trading_table(db):
    rows = db.execute("select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                      "where n.nspname = 'trading' and c.relkind = 'r' and not c.relrowsecurity").fetchall()
    assert rows == []


def _portfolio(db) -> str:
    owner = uuid.uuid4()
    return db.execute("insert into trading.portfolios (label, owner_id, mode, currency, initial_equity) "
                      "values (%s, %s, 'SHADOW', 'USDT', '10000') returning id",
                      (f"p-{owner.hex[:8]}", owner)).fetchone()[0]


def test_unbalanced_journal_entry_is_rejected_at_commit(db):
    pid = _portfolio(db)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            e = db.execute("insert into trading.journal_entries (portfolio_id, source_event_key, currency, occurred_at) "
                           "values (%s, 'evt-unbalanced', 'USDT', now()) returning id", (pid,)).fetchone()[0]
            db.execute("insert into trading.journal_lines values (%s, 1, 'cash', '-100'), (%s, 2, 'btc_at_cost', '99')",
                       (e, e))
    with db.transaction():
        e = db.execute("insert into trading.journal_entries (portfolio_id, source_event_key, currency, occurred_at) "
                       "values (%s, 'evt-balanced', 'USDT', now()) returning id", (pid,)).fetchone()[0]
        db.execute("insert into trading.journal_lines values (%s, 1, 'cash', '-100'), (%s, 2, 'btc_at_cost', '100')",
                   (e, e))


def test_journal_and_audit_are_append_only(db):
    db.execute("insert into trading.audit_events (action, target) values ('test', 'x')")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("update trading.audit_events set reason = 'edit'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("delete from trading.journal_lines")


def test_live_portfolio_needs_an_account(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into trading.portfolios (label, owner_id, mode, currency, initial_equity) "
                   "values ('live-no-account', gen_random_uuid(), 'LIVE', 'USDT', '1')")


def _sync_sql(tmp_path, label: str) -> str:
    import json
    import sys

    state = {
        "rules_version": "pathA-test", "start_equity": 10000.0,
        "journal": [{"date": "2026-10-13", "at": "2026-10-15T06:39:03+00:00", "kind": "open", "weight": 1.0, "notes": []}],
        "trades": [
            {"date": "2026-10-15", "at": "2026-10-15T07:00:00+00:00", "book": "paper", "side": "BUY",
             "price": 80016.0, "qty": 0.12484, "value": 10000.0},
            {"date": "2026-10-15", "at": "2026-10-15T07:00:00+00:00", "book": "buy_and_hold", "side": "BUY",
             "price": 80016.0, "qty": 0.12484, "value": 10000.0},
            {"date": "2026-10-20", "at": "2026-10-20T09:00:00+00:00", "book": "paper", "side": "SELL",
             "price": 85000.0, "qty": 0.12484, "value": 10600.0},
        ],
        "history": [{"date": "2026-10-15", "close": 81000.0, "weight_held_at_close": 1.0, "equity": 10100.0,
                     "buy_and_hold": 10100.0}],
    }
    state["rules_version"] = label
    (tmp_path / label).mkdir()
    (tmp_path / label / "state.json").write_text(json.dumps(state))
    return subprocess.run([sys.executable, str(ROOT / "scripts/trading_sync_sql.py"), "--root", str(tmp_path),
                           "--commit", f"{label}=abcdef1"], check=True, capture_output=True, text=True).stdout


_LEDGER_FINGERPRINT = """
    select (select count(*) from trading.journal_entries e join trading.portfolios p on p.id = e.portfolio_id
              where p.label like %(l)s),
           (select count(*) from trading.journal_lines l join trading.journal_entries e on e.id = l.entry_id
              join trading.portfolios p on p.id = e.portfolio_id where p.label like %(l)s),
           (select coalesce(sum(l.amount), 0) from trading.journal_lines l join trading.journal_entries e on e.id = l.entry_id
              join trading.portfolios p on p.id = e.portfolio_id where p.label like %(l)s),
           (select count(*) from trading.equity_snapshots s join trading.portfolios p on p.id = s.portfolio_id
              where p.label like %(l)s),
           (select count(*) from trading.portfolios where label like %(l)s),
           (select count(*) from trading.strategy_versions where label = %(v)s)
"""


def _fingerprint(db, label: str):
    return db.execute(_LEDGER_FINGERPRINT, {"l": f"{label}/%", "v": label}).fetchone()


def test_paper_book_sync_is_balanced_and_idempotent(db, tmp_path):
    sql = _sync_sql(tmp_path, "pathA-test")
    db.execute("insert into trading.workspace_settings (owner_id) values (gen_random_uuid()) on conflict do nothing")
    db.execute(sql)
    first = _fingerprint(db, "pathA-test")
    db.execute(sql)                                   # second and third runs change nothing
    db.execute(sql)
    assert _fingerprint(db, "pathA-test") == first
    n = db.execute("select count(*) from trading.journal_entries where source_event_key like 'pathA-test/%'").fetchone()[0]
    assert n == 5                                     # 2 fundings + 3 fills
    bad = db.execute("select entry_id from trading.journal_lines l join trading.journal_entries e on e.id = l.entry_id "
                     "where e.source_event_key like 'pathA-test/%' group by entry_id having sum(amount) <> 0").fetchall()
    assert bad == []
    snaps = db.execute("select count(*) from trading.equity_snapshots s join trading.portfolios p on p.id = s.portfolio_id "
                       "where p.label like 'pathA-test/%'").fetchone()[0]
    assert snaps == 2


def test_ledger_balances_per_currency(db, tmp_path):
    db.execute("insert into trading.workspace_settings (owner_id) values (gen_random_uuid()) on conflict do nothing")
    db.execute(_sync_sql(tmp_path, "pathA-ccy"))
    rows = db.execute("select e.currency, sum(l.amount) from trading.journal_lines l "
                      "join trading.journal_entries e on e.id = l.entry_id "
                      "join trading.portfolios p on p.id = e.portfolio_id where p.label like 'pathA-ccy/%' "
                      "group by e.currency").fetchall()
    assert rows and all(total == 0 for _, total in rows)
    # every entry is single-currency and balanced on its own
    bad = db.execute("select e.id from trading.journal_entries e join trading.journal_lines l on l.entry_id = e.id "
                     "join trading.portfolios p on p.id = e.portfolio_id where p.label like 'pathA-ccy/%' "
                     "group by e.id having sum(l.amount) <> 0").fetchall()
    assert bad == []


def test_sync_that_fails_midway_leaves_nothing(db, tmp_path):
    db.execute("insert into trading.workspace_settings (owner_id) values (gen_random_uuid()) on conflict do nothing")
    sql = _sync_sql(tmp_path, "pathA-crash")
    lines = sql.strip().splitlines()
    assert lines[0] == "begin;" and lines[-1] == "commit;"
    # fail after portfolios and journal entries are written, before commit
    broken = "\n".join(lines[:-2] + ["select 1/0;"] + lines[-2:])
    with pytest.raises(psycopg.errors.DivisionByZero):
        db.execute(broken)
    db.execute("rollback")
    assert _fingerprint(db, "pathA-crash") == (0, 0, 0, 0, 0, 0)
    db.execute(sql)                                   # a clean rerun then lands everything once
    assert _fingerprint(db, "pathA-crash")[0] == 5


def test_rebuild_from_repo_matches_supabase_schema(db):
    """A fresh database built only from supabase/migrations has the schema recorded from the live project.

    supabase/schema_fingerprint.txt was taken from project asakantrading with the same query; when a
    migration changes, apply it there first, then refresh the file from the live result.
    """
    n, h = db.execute((ROOT / "supabase/tests/schema_fingerprint.sql").read_text()).fetchone()
    assert f"{n} {h}" == (ROOT / "supabase/schema_fingerprint.txt").read_text().strip()


def test_migration_files_match_recorded_history():
    """File names must equal the versions Supabase recorded, so `supabase db push` never re-runs one."""
    recorded = (ROOT / "supabase/migration_history.txt").read_text().split()
    files = sorted(p.stem for p in (ROOT / "supabase/migrations").glob("*.sql"))
    assert files == recorded


def test_revoked_role_is_not_returned_by_the_api_role_query(db):
    from api.auth import ACTIVE_ROLES_SQL

    uid = uuid.uuid4()
    db.execute("begin")
    try:
        db.execute("insert into trading.operator_roles (user_id, role) values (%s, 'viewer'), (%s, 'operator')",
                   (uid, uid))
        assert {r[0] for r in db.execute(ACTIVE_ROLES_SQL, (uid,)).fetchall()} == {"viewer", "operator"}
        db.execute("update trading.operator_roles set active = false where user_id = %s and role = 'operator'", (uid,))
        assert {r[0] for r in db.execute(ACTIVE_ROLES_SQL, (uid,)).fetchall()} == {"viewer"}
        db.execute("update trading.operator_roles set active = false where user_id = %s", (uid,))
        assert db.execute(ACTIVE_ROLES_SQL, (uid,)).fetchall() == []
    finally:
        db.execute("rollback")


def test_overview_sql_takes_each_portfolios_latest_snapshot(db):
    from api.portfolios import OVERVIEW_SQL

    pid = _portfolio(db)
    db.execute("insert into trading.equity_snapshots (portfolio_id, at, trading_equity, economic_equity, high_water_mark) "
               "values (%s, '2026-10-01', '10000', '10000', '10000'), (%s, '2026-10-02', '10050.5', '10050.25', '10050.25')",
               (pid, pid))
    rows = {r[0]: r for r in db.execute(OVERVIEW_SQL).fetchall()}
    label = db.execute("select label from trading.portfolios where id = %s", (pid,)).fetchone()[0]
    r = rows[label]
    assert str(r[4].date()) == "2026-10-02" and r[5].startswith("10050.5") and r[6].startswith("10050.25")


def _v12_state(halt: bool = False) -> dict:
    """A real pathA-v1.2 book: open, buy, a few marks, then a sell (or a HALT that sells)."""
    import pandas as pd

    from trading.shadow.rebalance_book import PathABook

    d0 = pd.Timestamp("2026-10-15", tz="UTC")
    closes = [80000.0, 81000.0, 79000.0, 82000.0] if not halt else [80000.0, 81000.0, 58000.0, 57000.0]
    hourly = pd.DataFrame(
        [(d0 + pd.Timedelta(days=i, hours=h), c, c) for i, c in enumerate(closes) for h in range(24)],
        columns=["t", "open", "close"]).set_index("t")
    daily = pd.DataFrame({"close": hourly["close"].resample("1D").last()})
    book = PathABook()
    at = d0 + pd.Timedelta(days=1, minutes=5)
    book.open("2026-10-15", 1.0, at, at, ["open"])
    for i in range(1, len(closes)):
        day = d0 + pd.Timedelta(days=i)
        book.advance(daily.loc[:day], hourly.loc[:day + pd.Timedelta(hours=23)])
        at = day + pd.Timedelta(days=1, minutes=5)
        book.decide(str(day.date()), 0.0 if i == 2 and not halt else 1.0, at, at, ["x"])
    return json.loads(json.dumps(book.to_dict()))


def _v12_sql(tmp_path, state: dict) -> str:
    import sys

    v = state["rules_version"]
    (tmp_path / v).mkdir()
    (tmp_path / v / "state.json").write_text(json.dumps(state))
    extra = [] if v == "pathA-v1.2" else ["--commit", f"{v}=abcdef1"]
    return subprocess.run([sys.executable, str(ROOT / "scripts/trading_sync_sql.py"), "--root", str(tmp_path), *extra],
                          check=True, capture_output=True, text=True).stdout


@pytest.mark.parametrize("halt", [False, True])
def test_pathA_v12_sync_copies_the_spot_journal(db, tmp_path, halt):
    from decimal import Decimal

    state = _v12_state(halt)
    if halt:                                  # its own label: the journal is append-only within this database
        state["rules_version"] = "pathA-v1.2-halt"
    label = state["rules_version"]
    sql = _v12_sql(tmp_path, state)
    db.execute("insert into trading.workspace_settings (owner_id) values (gen_random_uuid()) on conflict do nothing")
    db.execute(sql)
    first = _fingerprint(db, label)
    db.execute(sql)
    assert _fingerprint(db, label) == first                     # re-running inserts nothing
    for book, key in (("paper", "book"), ("buy_and_hold", "buy_and_hold")):
        entries = state[key]["entries"]
        n = db.execute("select count(*) from trading.journal_entries where source_event_key like %s",
                       (f"{label}/{book}:%",)).fetchone()[0]
        assert n == len(entries)
        # account balances in the database equal the book's own balances, to the last digit
        rows = dict(db.execute(
            "select l.ledger_account, sum(l.amount) from trading.journal_lines l join trading.journal_entries e "
            "on e.id = l.entry_id where e.source_event_key like %s group by 1", (f"{label}/{book}:%",)).fetchall())
        want: dict[str, Decimal] = {}
        for e in entries:
            for a, x in e["lines"]:
                a = {"capital": "owner_capital", "inventory_at_cost": "btc_position_cost"}.get(a, a)
                want[a] = want.get(a, Decimal(0)) + Decimal(x)
        assert {a: v for a, v in rows.items() if v} == {a: v for a, v in want.items() if v}
        last = db.execute("select trading_equity from trading.equity_snapshots s join trading.portfolios p "
                          "on p.id = s.portfolio_id where p.label = %s order by at desc limit 1",
                          (f"{label}/{book}",)).fetchone()[0]
        h = state["history"][-1]
        assert last == Decimal(h["equity"] if book == "paper" else h["buy_and_hold"]).quantize(Decimal("1e-12"))
    bad = db.execute("select entry_id from trading.journal_lines l join trading.journal_entries e on e.id = l.entry_id "
                     "where e.source_event_key like %s group by entry_id having sum(amount) <> 0", (f"{label}/%",)).fetchall()
    assert bad == []
    sells = sum(1 for e in state["book"]["entries"] if e["data"].get("side") == "SELL")
    assert sells == 1 and (state["risk"]["halted"] is halt)
