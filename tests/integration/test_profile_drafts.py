"""Draft bot profiles stored in trading.bot_profile_drafts, on a throwaway local PostgreSQL."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.main import create_app
from api.profile_drafts import DraftConflict, PgDraftStore, SaveDraft
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.config import Settings
from trading.contracts.enums import OperatorRole

psycopg_pool = pytest.importorskip("psycopg_pool")

VIEWER, OPERATOR = UUID(int=1), UUID(int=2)


class Verifier:
    def verify(self, token):
        try:
            return UUID(token)
        except ValueError as e:
            raise AuthError("invalid_token", "invalid") from e


class Roles:
    async def active_roles(self, user):
        return frozenset({OperatorRole.OPERATOR if user == OPERATOR else OperatorRole.VIEWER})


def recipe(**over):
    return {"name": "Profile 1", "entry": {"number": 1, "version": "1.0", "timeframe": "4h", "params": {}}, **over}


def headers(user=OPERATOR):
    return {"Authorization": f"Bearer {user}"}


def run(db, work: Callable[[PgDraftStore], Awaitable[Any]]) -> Any:  # noqa: F811
    """Run ``work`` against a store on its own pool and event loop."""
    async def go() -> Any:
        async with psycopg_pool.AsyncConnectionPool(db.info.dsn, min_size=2, max_size=4) as pool:
            return await work(PgDraftStore(pool))
    return asyncio.run(go())


@pytest.fixture
def rig(db):  # noqa: F811
    db.execute("truncate trading.bot_profile_drafts")
    pool = psycopg_pool.AsyncConnectionPool(db.info.dsn, open=False)
    app = create_app(Settings(), Verifier(), Roles(), drafts=PgDraftStore(pool))
    app.router.on_startup.append(pool.open)
    app.router.on_shutdown.append(pool.close)
    with TestClient(app) as client:
        yield client, db


def test_role_gates_and_persistence_with_normalized_catalog_defaults(rig):
    client, conn = rig
    endpoint = "/api/v1/bot-profile-drafts"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=headers(VIEWER)).json() == {"items": [], "can_edit": False}
    assert client.post(endpoint, headers=headers(VIEWER), json={"profile": recipe()}).status_code == 403
    response = client.post(endpoint, headers=headers(), json={"profile": recipe()})
    assert response.status_code == 200
    saved = response.json()
    assert saved["revision"] == 1 and saved["execution_supported"] is False
    assert saved["profile"]["status"] == "draft" and saved["saved_by"] == str(OPERATOR)
    assert saved["profile"]["entry"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
    assert client.get(endpoint, headers=headers(VIEWER)).json()["items"] == [saved]
    assert run(conn, lambda s: s.list_latest()) == [saved]


def test_idempotent_retry_and_stale_edit_preserve_revisions(rig):
    _, conn = rig
    create = SaveDraft(profile=recipe(), request_id=uuid4())
    one = run(conn, lambda s: s.save(create, OPERATOR))
    assert run(conn, lambda s: s.save(create, OPERATOR)) == one  # retry after an answer was lost
    edit = SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name="Revised"))
    two = run(conn, lambda s: s.save(edit, OPERATOR))
    assert two["revision"] == 2 and two["profile_id"] == one["profile_id"]
    assert run(conn, lambda s: s.save(edit, OPERATOR)) == two
    with pytest.raises(DraftConflict):
        run(conn, lambda s: s.save(SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name="Stale")), OPERATOR))
    with pytest.raises(DraftConflict):                                  # same key, different payload
        run(conn, lambda s: s.save(SaveDraft(profile=recipe(name="Other"), request_id=create.request_id), OPERATOR))
    assert run(conn, lambda s: s.list_latest()) == [two]
    rows = conn.execute("select revision, body ->> 'name' from trading.bot_profile_drafts order by revision").fetchall()
    assert rows == [(1, "Profile 1"), (2, "Revised")]
    with pytest.raises(Exception, match="trading.bot_profile_drafts"):  # revisions are append-only
        conn.execute("update trading.bot_profile_drafts set revision = 9")


def test_two_editors_cannot_overwrite_the_same_revision(rig):
    _, conn = rig
    one = run(conn, lambda s: s.save(SaveDraft(profile=recipe()), OPERATOR))

    async def both(store: PgDraftStore) -> list[Any]:
        async def edit(name: str) -> Any:
            try:
                return (await store.save(SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name=name)),
                                         OPERATOR))["revision"]
            except DraftConflict:
                return "conflict"
        return list(await asyncio.gather(edit("Editor A"), edit("Editor B")))
    results = run(conn, both)
    assert results.count(2) == 1 and results.count("conflict") == 1


def test_without_the_table_the_api_says_so(db):  # noqa: F811
    client = TestClient(create_app(Settings(), Verifier(), Roles()))
    response = client.get("/api/v1/bot-profile-drafts", headers=headers(VIEWER))
    assert response.status_code == 503 and response.json()["code"] == "drafts_unavailable"


@pytest.mark.parametrize("patch", [
    {"status": "active"}, {"bot_id": "worker"}, {"max_exposure_percent": 101},
    {"risk_per_trade_percent": 30, "max_exposure_percent": 20},
    {"entry": {"number": 999, "version": "1.0", "timeframe": "4h"}},
    {"entry": {"number": 1, "version": "wrong", "timeframe": "4h"}},
    {"entry": {"number": 1, "version": "1.0", "timeframe": "1d"}},
    {"entry": {"number": 1, "version": "1.0", "timeframe": "4h", "params": {"fast": 999}}},
    {"exit_kind": "strategy_signal"}, {"entry": None},
    {"stop_loss": {"kind": "fixed_percent", "percent": 2}},  # cannot silently carry ATR defaults too
])
def test_invalid_or_executable_recipes_are_rejected_by_api(rig, patch):
    client, conn = rig
    response = client.post("/api/v1/bot-profile-drafts", headers=headers(), json={"profile": recipe(**patch)})
    assert response.status_code == 422
    assert run(conn, lambda s: s.list_latest()) == []


def test_preset_keeps_its_full_combination_and_custom_recipe_stays_separate(rig):
    _, conn = rig
    preset = {"profile_id": "example", "version": 1, "name": "Example", "combine": "average",
              "strategies": [{"number": 1, "timeframe": "4h"}, {"number": 7, "timeframe": "1d", "params": {"sma": 100}}]}
    body = recipe(setup_mode="preset", timeframe="1d", entry=None, preset_profile=preset,
                  preset_strategy_versions={1: "1.0", 7: "1.0"})
    saved = run(conn, lambda s: s.save(SaveDraft(profile=body), OPERATOR))
    assert saved["profile"]["preset_profile"]["combine"] == "average"
    assert len(saved["profile"]["preset_profile"]["strategies"]) == 2
    assert saved["profile"]["entry"] is None and saved["execution_supported"] is False
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SaveDraft(profile={**body, "entry": recipe()["entry"]})
