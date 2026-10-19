"""The agent book tables (migration 20261019020014_agent_book), on a database rebuilt from supabase/migrations."""

import pytest

from tests.integration.test_migrations import db  # noqa: F401  (module fixture: a fresh local PostgreSQL)

psycopg = pytest.importorskip("psycopg")

NEW_SIGNAL = ("insert into trading.agent_signals (book, condition, params, expires_at) "
              "values (%s, 'time_check', '{}', now() + %s::interval)")
NEW_RUN = ("insert into trading.agent_runs (book, decision_id, role, model, prompt_version, started_at, outcome) "
           "values ('b', gen_random_uuid(), 'decider', 'm', 'v1', now(), 'OK') returning id")
NEW_CALL = "insert into trading.agent_tool_calls values (%s, %s, 'news', '{}', %s, null, %s, now())"


def test_signals_are_capped_at_five_active_and_live_at_most_72h(db):  # noqa: F811
    for _ in range(5):
        db.execute(NEW_SIGNAL, ("cap", "1 hour"))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute(NEW_SIGNAL, ("cap", "1 hour"))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(NEW_SIGNAL, ("ttl", "73 hours"))


def test_a_signal_ends_once_and_is_never_deleted(db):  # noqa: F811
    sid = db.execute(NEW_SIGNAL + " returning id", ("end", "1 hour")).fetchone()[0]
    db.execute("update trading.agent_signals set status = 'FIRED', ended_at = now(), fired_bar_open = now() where id = %s",
                     (sid,))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("update trading.agent_signals set status = 'EXPIRED' where id = %s", (sid,))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("delete from trading.agent_signals where id = %s", (sid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(NEW_SIGNAL.replace("'time_check'", "'run_code'"), ("end", "1 hour"))


def test_finished_runs_and_tool_calls_are_append_only(db):  # noqa: F811
    rid = db.execute(NEW_RUN).fetchone()[0]
    db.execute("update trading.agent_runs set finished_at = now() where id = %s", (rid,))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("update trading.agent_runs set cost_usd = 1 where id = %s", (rid,))
    db.execute(NEW_CALL, (rid, 1, "{}", None))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(NEW_CALL, (rid, 2, None, None))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("delete from trading.agent_tool_calls where run_id = %s", (rid,))


def test_rls_stays_on_for_every_trading_table(db):  # noqa: F811
    rows = db.execute("select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                            "where n.nspname = 'trading' and c.relkind = 'r' and not c.relrowsecurity").fetchall()
    assert rows == []
