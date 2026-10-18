"""The agent book SQL (supabase/pending/agent_book.sql, not applied yet) on top of every applied migration."""

from pathlib import Path

import pytest

from tests.integration.test_migrations import db  # noqa: F401  (module fixture: a fresh local PostgreSQL)

psycopg = pytest.importorskip("psycopg")
ROOT = Path(__file__).resolve().parents[2]

NEW_SIGNAL = ("insert into trading.agent_signals (book, condition, params, expires_at) "
              "values (%s, 'time_check', '{}', now() + %s::interval)")
NEW_RUN = ("insert into trading.agent_runs (book, decision_id, role, model, prompt_version, started_at, outcome) "
           "values ('b', gen_random_uuid(), 'decider', 'm', 'v1', now(), 'OK') returning id")
NEW_CALL = "insert into trading.agent_tool_calls values (%s, %s, 'news', '{}', %s, null, %s, now())"


@pytest.fixture(scope="module")
def agent_db(db):  # noqa: F811
    db.execute((ROOT / "supabase/pending/agent_book.sql").read_text())
    return db


def test_signals_are_capped_at_five_active_and_live_at_most_72h(agent_db):
    for _ in range(5):
        agent_db.execute(NEW_SIGNAL, ("cap", "1 hour"))
    with pytest.raises(psycopg.errors.RaiseException):
        agent_db.execute(NEW_SIGNAL, ("cap", "1 hour"))
    with pytest.raises(psycopg.errors.CheckViolation):
        agent_db.execute(NEW_SIGNAL, ("ttl", "73 hours"))


def test_a_signal_ends_once_and_is_never_deleted(agent_db):
    sid = agent_db.execute(NEW_SIGNAL + " returning id", ("end", "1 hour")).fetchone()[0]
    agent_db.execute("update trading.agent_signals set status = 'FIRED', ended_at = now(), fired_bar_open = now() where id = %s",
                     (sid,))
    with pytest.raises(psycopg.errors.RaiseException):
        agent_db.execute("update trading.agent_signals set status = 'EXPIRED' where id = %s", (sid,))
    with pytest.raises(psycopg.errors.RaiseException):
        agent_db.execute("delete from trading.agent_signals where id = %s", (sid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        agent_db.execute(NEW_SIGNAL.replace("'time_check'", "'run_code'"), ("end", "1 hour"))


def test_finished_runs_and_tool_calls_are_append_only(agent_db):
    rid = agent_db.execute(NEW_RUN).fetchone()[0]
    agent_db.execute("update trading.agent_runs set finished_at = now() where id = %s", (rid,))
    with pytest.raises(psycopg.errors.RaiseException):
        agent_db.execute("update trading.agent_runs set cost_usd = 1 where id = %s", (rid,))
    agent_db.execute(NEW_CALL, (rid, 1, "{}", None))
    with pytest.raises(psycopg.errors.CheckViolation):
        agent_db.execute(NEW_CALL, (rid, 2, None, None))
    with pytest.raises(psycopg.errors.RaiseException):
        agent_db.execute("delete from trading.agent_tool_calls where run_id = %s", (rid,))


def test_rls_stays_on_for_every_trading_table(agent_db):
    rows = agent_db.execute("select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                            "where n.nspname = 'trading' and c.relkind = 'r' and not c.relrowsecurity").fetchall()
    assert rows == []
