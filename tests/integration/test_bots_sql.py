"""trading.bots / trading.bot_approvals rules on a throwaway local PostgreSQL (m4-bot-config-plan.md G4, G6, G3)."""

import uuid

import pytest

from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.bots import config

psycopg = pytest.importorskip("psycopg")

OPERATOR, VIEWER = uuid.UUID(int=101), uuid.UUID(int=102)


def body(account="BOT1", **over):
    b = {"name": "R10a v2", "account": account, "venue": "SPOT_TESTNET",
         "instrument": {"symbol": "BTCUSDT", "market": "SPOT"},
         "strategy": {"template": "tn-r10a-code-v2", "version": "tn-r10a-code-v2", "timeframe": "1d", "params": {}},
         "allocation": {"asset": "USDT", "amount": "2000"}, "mode": "SHADOW", **over}
    return config.normalize(config.parse(b))


@pytest.fixture
def conn(db):  # noqa: F811
    db.execute("truncate trading.bot_approvals, trading.bots")
    db.execute("delete from trading.operator_roles where user_id in (%s, %s)", (OPERATOR, VIEWER))
    db.execute("insert into trading.operator_roles (user_id, role) values (%s, 'operator'), (%s, 'viewer')", (OPERATOR, VIEWER))
    return db


def save(conn, bot, rev, account="BOT1", **over):
    b, h = body(account, **over)
    conn.execute("insert into trading.bots (bot_id, revision, account, body, hash, saved_by, request_id) "
                 "values (%s, %s, %s, %s, %s, %s, %s)", (bot, rev, account, psycopg.types.json.Jsonb(b), h, OPERATOR, uuid.uuid4()))
    return h


def event(conn, bot, rev, h, action, actor=OPERATOR):
    conn.execute("insert into trading.bot_approvals (bot_id, revision, hash, action, actor) values (%s, %s, %s, %s, %s)",
                 (bot, rev, h, action, actor))


def test_append_only(conn):
    bot = uuid.uuid4()
    h = save(conn, bot, 1)
    event(conn, bot, 1, h, "submit")
    for sql in ("update trading.bots set hash = hash", "delete from trading.bots",
                "update trading.bot_approvals set note = 'x'", "delete from trading.bot_approvals"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute(sql)


def test_revisions_in_order_and_same_account(conn):
    bot = uuid.uuid4()
    with pytest.raises(psycopg.errors.RaiseException, match="starts at revision 1"):
        save(conn, bot, 2)
    save(conn, bot, 1)
    for rev in (1, 3):                                  # a second writer of revision 1, or a skipped revision
        with pytest.raises(psycopg.errors.RaiseException, match="next revision is 2"):
            save(conn, bot, rev)
    with pytest.raises(psycopg.errors.RaiseException, match="cannot move it"):
        save(conn, bot, 2, account="BOT2")
    save(conn, bot, 2, mode="TESTNET")


def test_body_checks(conn):
    b, h = body()
    for bad in ({**b, "mode": "LIVE"}, {**b, "account": "OTHER"}, {**b, "venue": "SPOT_MAINNET"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("insert into trading.bots (bot_id, revision, account, body, hash, saved_by, request_id) "
                         "values (%s, 1, 'BOT1', %s, %s, %s, %s)",
                         (uuid.uuid4(), psycopg.types.json.Jsonb(bad), h, OPERATOR, uuid.uuid4()))


def test_event_hash_must_match_revision(conn):
    bot = uuid.uuid4()
    save(conn, bot, 1)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        event(conn, bot, 1, "0" * 64, "submit")


def test_only_operator_and_approve_after_submit(conn):
    bot = uuid.uuid4()
    h = save(conn, bot, 1)
    with pytest.raises(psycopg.errors.RaiseException, match="active operator or admin"):
        event(conn, bot, 1, h, "submit", actor=VIEWER)
    with pytest.raises(psycopg.errors.RaiseException, match="was not submitted"):
        event(conn, bot, 1, h, "approve")
    event(conn, bot, 1, h, "submit")
    event(conn, bot, 1, h, "approve")
    with pytest.raises(psycopg.errors.UniqueViolation):
        event(conn, bot, 1, h, "reject")
    conn.execute("update trading.operator_roles set active = false where user_id = %s", (OPERATOR,))
    h2 = save(conn, bot, 2)
    with pytest.raises(psycopg.errors.RaiseException, match="active operator or admin"):
        event(conn, bot, 2, h2, "submit")


def test_one_approved_bot_per_account_and_retire(conn):
    a, b = uuid.uuid4(), uuid.uuid4()
    ha, hb = save(conn, a, 1), save(conn, b, 1)
    for bot, h in ((a, ha), (b, hb)):
        event(conn, bot, 1, h, "submit")
    event(conn, a, 1, ha, "approve")
    with pytest.raises(psycopg.errors.RaiseException, match="one bot per account"):
        event(conn, b, 1, hb, "approve")
    event(conn, a, 1, ha, "retire")
    event(conn, b, 1, hb, "approve")
    with pytest.raises(psycopg.errors.RaiseException, match="is retired"):
        save(conn, a, 2)
    with pytest.raises(psycopg.errors.RaiseException, match="is retired"):
        event(conn, a, 1, ha, "reject")
    c = uuid.uuid4()
    hc = save(conn, c, 1, account="BOT2")              # another account is free
    event(conn, c, 1, hc, "submit")
    event(conn, c, 1, hc, "approve")
