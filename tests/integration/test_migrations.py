"""Apply every Supabase migration to a throwaway local PostgreSQL and check grants and invariants.

Skipped when PostgreSQL server binaries are not installed (set PG_BIN to their directory).
"""

from __future__ import annotations

import glob
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
