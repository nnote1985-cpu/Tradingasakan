from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.main import create_app
from api.profile_drafts import DraftConflict, DraftStore, SaveDraft
from trading.config import Settings
from trading.contracts.enums import OperatorRole

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


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "drafts.sqlite3"
    store = DraftStore(path)
    client = TestClient(create_app(Settings(), Verifier(), Roles(), drafts=store))
    return client, store, path


def test_role_gates_and_persistence_with_normalized_catalog_defaults(rig):
    client, store, path = rig
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
    assert DraftStore(path).list_latest() == store.list_latest() == [saved]


def test_idempotent_retry_and_stale_edit_preserve_revisions(rig):
    _, store, path = rig
    create = SaveDraft(profile=recipe(), request_id=uuid4())
    one = store.save(create, OPERATOR)
    assert store.save(create, OPERATOR) == one  # retry after an answer was lost
    edit = SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name="Revised"))
    two = store.save(edit, OPERATOR)
    assert two["revision"] == 2 and two["profile_id"] == one["profile_id"]
    assert store.save(edit, OPERATOR) == two
    with pytest.raises(DraftConflict):
        store.save(SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name="Stale")), OPERATOR)
    assert DraftStore(path).list_latest() == [two]
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("select revision, body from draft_revisions order by revision").fetchall()
    assert len(rows) == 2 and '"Profile 1"' in rows[0][1]


def test_two_editors_cannot_overwrite_the_same_revision(rig):
    _, store, _ = rig
    one = store.save(SaveDraft(profile=recipe()), OPERATOR)

    def edit(name):
        try:
            return store.save(SaveDraft(profile_id=one["profile_id"], expected_revision=1, profile=recipe(name=name)), OPERATOR)["revision"]
        except DraftConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["Editor A", "Editor B"]))
    assert results.count(2) == 1 and results.count("conflict") == 1


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
    client, store, _ = rig
    response = client.post("/api/v1/bot-profile-drafts", headers=headers(), json={"profile": recipe(**patch)})
    assert response.status_code == 422
    assert store.list_latest() == []


def test_preset_keeps_its_full_combination_and_custom_recipe_stays_separate(rig):
    _, store, _ = rig
    preset = {"profile_id": "example", "version": 1, "name": "Example", "combine": "average",
              "strategies": [{"number": 1, "timeframe": "4h"}, {"number": 7, "timeframe": "1d", "params": {"sma": 100}}]}
    body = recipe(setup_mode="preset", timeframe="1d", entry=None, preset_profile=preset,
                  preset_strategy_versions={1: "1.0", 7: "1.0"})
    saved = store.save(SaveDraft(profile=body), OPERATOR)
    assert saved["profile"]["preset_profile"]["combine"] == "average"
    assert len(saved["profile"]["preset_profile"]["strategies"]) == 2
    assert saved["profile"]["entry"] is None and saved["execution_supported"] is False
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SaveDraft(profile={**body, "entry": recipe()["entry"]})
