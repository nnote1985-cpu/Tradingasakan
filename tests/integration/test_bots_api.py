"""Bot API on a throwaway local PostgreSQL (m4-bot-config-plan.md §3.4; G3, G4, G5, G6, G10)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.bots import PgBotStore, diff
from api.main import create_app
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.config import Settings
from trading.contracts.enums import OperatorRole

psycopg_pool = pytest.importorskip("psycopg_pool")
psycopg = pytest.importorskip("psycopg")

VIEWER, OPERATOR = UUID(int=201), UUID(int=202)
NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


class Verifier:
    def verify(self, token):
        try:
            return UUID(token)
        except ValueError as e:
            raise AuthError("invalid_token", "invalid") from e


class Roles:
    async def active_roles(self, user):
        return frozenset({OperatorRole.OPERATOR if user == OPERATOR else OperatorRole.VIEWER})


def h(user=OPERATOR):
    return {"Authorization": f"Bearer {user}"}


def cfg(account="BOT1", **over):
    return {"name": "R10a v2", "account": account, "venue": "SPOT_TESTNET",
            "instrument": {"symbol": "BTCUSDT", "market": "SPOT"},
            "strategy": {"template": "tn-r10a-code-v2", "version": "tn-r10a-code-v2", "timeframe": "1d", "params": {}},
            "allocation": {"asset": "USDT", "amount": "2000"}, "mode": "SHADOW", **over}


def seed_account(db, account, usdt="10000", age=timedelta(minutes=2), verdict="MATCH", body=None):  # noqa: F811
    at = NOW - age
    db.execute("insert into trading.exec_account_epochs (account, epoch, venue, status, opened_at, opening_balances) "
               "values (%s, 1, 'BINANCE_SPOT_TESTNET', 'OPEN', %s, '{}')", (account, at))
    db.execute("insert into trading.exec_balances (account, epoch, asset, free, locked, as_of) values (%s, 1, 'USDT', %s, 0, %s)",
               (account, usdt, at))
    db.execute("insert into trading.exec_reconciliations (account, epoch, checked_at, verdict, first_check, open_orders) "
               "values (%s, 1, %s, %s, false, 0)", (account, at, verdict))
    db.execute("insert into trading.worker_heartbeats (account, owner, host, started_at, last_tick_at, body, updated_at) "
               "values (%s, 'o', 'h', %s, %s, %s, %s)", (account, at, at, psycopg.types.json.Jsonb(body or {}), at))


@pytest.fixture
def rig(db):  # noqa: F811
    db.execute("truncate trading.bot_approvals, trading.bots, trading.worker_heartbeats, trading.exec_reconciliations, "
               "trading.exec_balances, trading.exec_account_epochs cascade")
    db.execute("delete from trading.operator_roles where user_id in (%s, %s)", (OPERATOR, VIEWER))
    db.execute("insert into trading.operator_roles (user_id, role) values (%s, 'operator'), (%s, 'viewer')", (OPERATOR, VIEWER))
    seed_account(db, "BOT1")
    seed_account(db, "DEFAULT", body={"execution": {"safety": "RUNNING", "r10a": {"book": "tn-r10a-code-v1"}}})
    pool = psycopg_pool.AsyncConnectionPool(db.info.dsn, open=False)
    app = create_app(Settings(), Verifier(), Roles(), bots=PgBotStore(pool, now=lambda: NOW))
    app.router.on_startup.append(pool.open)
    app.router.on_shutdown.append(pool.close)
    with TestClient(app) as client:
        yield client, db


def save(c, conf=None, bot_id=None, expected=0, rid=None, user=OPERATOR):
    return c.post("/api/v1/bots", headers=h(user), json={"bot_id": bot_id, "expected_revision": expected,
                                                         "request_id": str(rid or uuid4()), "config": conf or cfg()})


def act(c, bot, rev, action, user=OPERATOR):
    return c.post(f"/api/v1/bots/{bot}/revisions/{rev}/{action}", headers=h(user), json={"note": "ok"})


def test_save_submit_approve_flow(rig):
    c, _ = rig
    r = save(c)
    assert r.status_code == 200, r.text
    bot = r.json()["bot_id"]
    assert r.json()["revision"] == 1 and len(r.json()["hash"]) == 64
    assert c.get("/api/v1/bots", headers=h(VIEWER)).json()["bots"][0]["status"] == "draft"
    assert act(c, bot, 1, "approve").json()["code"] == "not_submitted"
    assert act(c, bot, 1, "submit").json()["status"] == "submitted"
    got = act(c, bot, 1, "approve").json()
    assert got["status"] == "approved" and got["approved"]["revision"] == 1 and got["approved"]["actor"] == str(OPERATOR)
    assert act(c, bot, 1, "approve").json()["code"] == "already_decided"
    # a new revision does not replace the approved one until it is approved itself
    r2 = save(c, cfg(mode="TESTNET"), bot_id=bot, expected=1)
    assert r2.json()["revision"] == 2
    one = c.get(f"/api/v1/bots/{bot}", headers=h(VIEWER)).json()
    assert one["approved"]["revision"] == 1 and one["latest"]["revision"] == 2 and one["status"] == "approved"
    assert one["revisions"][1]["diff"] == [{"field": "mode", "before": "SHADOW", "after": "TESTNET"}]
    assert one["can_edit"] is False


def test_viewer_cannot_write(rig):
    c, _ = rig
    assert save(c, user=VIEWER).status_code == 403
    bot = save(c).json()["bot_id"]
    assert act(c, bot, 1, "submit", user=VIEWER).status_code == 403
    assert c.post(f"/api/v1/bots/{bot}/retire", headers=h(VIEWER), json={}).status_code == 403


def test_field_errors(rig):
    c, _ = rig
    r = save(c, cfg(mode="LIVE", allocation={"asset": "USDT", "amount": "5000"}))
    assert r.status_code == 422
    assert {e["field"]: e["code"] for e in r.json()["errors"]} == {"mode": "live_refused", "allocation.amount": "allocation_bounds"}
    r = save(c, {**cfg(), "api_secret": "x"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "api_secret"


def test_idempotent_and_conflict(rig):
    c, _ = rig
    rid = uuid4()
    a = save(c, rid=rid).json()
    assert save(c, rid=rid).json() == a                         # a lost response, retried
    assert save(c, cfg(name="other"), rid=rid).json()["code"] == "request_reused"
    bot = a["bot_id"]
    assert save(c, bot_id=bot, expected=0).status_code == 409   # stale
    assert save(c, cfg(account="BOT2"), bot_id=bot, expected=1).json()["code"] == "account_fixed"


def test_one_bot_per_account(rig):
    c, _ = rig
    save(c)
    r = save(c)
    assert r.status_code == 409 and r.json()["code"] == "account_in_use"
    r = save(c, cfg(account="DEFAULT"))
    assert r.status_code == 409 and "env" in r.json()["message"]
    accounts = {a["account"]: a for a in c.get("/api/v1/bot-accounts", headers=h(VIEWER)).json()["accounts"]}
    assert accounts["BOT1"]["available"] is False and accounts["DEFAULT"]["available"] is False
    assert accounts["BOT1"]["usdt_free"] == "10000.000000000000000000"


def test_approve_checks_funds(rig):
    c, db = rig  # noqa: F811
    seed_account(db, "POOR", usdt="1500")
    seed_account(db, "OLD", age=timedelta(minutes=30))
    for account, code in (("POOR", "over_free_balance"), ("OLD", "balance_stale")):
        bot = save(c, cfg(account=account)).json()["bot_id"]
        act(c, bot, 1, "submit")
        r = act(c, bot, 1, "approve")
        assert r.status_code == 409 and r.json()["code"] == code
    bot = save(c, cfg(account="NOWORKER")).json()["bot_id"]
    act(c, bot, 1, "submit")
    assert act(c, bot, 1, "approve").json()["code"] == "no_balance"


def test_reject_then_new_revision(rig):
    c, _ = rig
    bot = save(c).json()["bot_id"]
    act(c, bot, 1, "submit")
    assert act(c, bot, 1, "reject").json()["status"] == "draft"
    save(c, cfg(allocation={"asset": "USDT", "amount": "1500"}), bot_id=bot, expected=1)
    assert act(c, bot, 1, "approve").json()["code"] == "not_latest"
    act(c, bot, 2, "submit")
    assert act(c, bot, 2, "approve").json()["approved"]["revision"] == 2


def test_running_comes_from_heartbeat_and_retire_needs_halt(rig):
    c, db = rig  # noqa: F811
    bot = save(c).json()["bot_id"]
    act(c, bot, 1, "submit")
    approved = act(c, bot, 1, "approve").json()
    beat = {"execution": {"safety": "RUNNING", "bot": {"bot_id": bot, "revision": 1, "hash": approved["approved"]["hash"],
                                                       "mode": "SHADOW"}}}
    db.execute("update trading.worker_heartbeats set body = %s where account = 'BOT1'", (psycopg.types.json.Jsonb(beat),))
    got = c.get("/api/v1/bots", headers=h(VIEWER)).json()["bots"][0]
    assert got["status"] == "running" and got["running"]["revision"] == 1
    r = c.post(f"/api/v1/bots/{bot}/retire", headers=h(), json={"note": "x"})
    assert r.status_code == 409 and r.json()["code"] == "still_running"
    beat["execution"]["safety"] = "HALTED"
    db.execute("update trading.worker_heartbeats set body = %s where account = 'BOT1'", (psycopg.types.json.Jsonb(beat),))
    assert c.get("/api/v1/bots", headers=h(VIEWER)).json()["bots"][0]["status"] == "halted"
    assert c.post(f"/api/v1/bots/{bot}/retire", headers=h(), json={}).json()["status"] == "retired"
    assert save(c, bot_id=bot, expected=1).json()["code"] == "retired"
    assert save(c).status_code == 200                             # the account is free again


def test_source_draft_must_exist(rig):
    c, _ = rig
    r = save(c, cfg(source_draft={"profile_id": str(uuid4()), "revision": 1}))
    assert r.status_code == 422 and r.json()["code"] == "draft_not_found"


def test_templates_listed(rig):
    c, _ = rig
    t = c.get("/api/v1/bot-templates", headers=h(VIEWER)).json()["templates"]
    assert [x["template"] for x in t] == ["tn-r10a-code-v2"] and t[0]["allocation"]["max"] == "2000"


def test_diff_helper():
    assert diff(None, {"a": {"b": 1}}) == [{"field": "a.b", "before": None, "after": 1}]
    assert diff({"a": 1}, {"a": 1}) == []
