import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from api.auth import SupabaseJwtVerifier
from api.main import create_app
from trading.config import Settings
from trading.contracts.enums import OperatorRole

URL = "https://example.supabase.co"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER = ec.generate_private_key(ec.SECP256R1())


class _Jwks:
    """Stands in for PyJWKClient: returns the test public key."""
    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict({**jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "alg": "ES256"})


class _Roles:
    def __init__(self, table):
        self.table = table

    async def active_roles(self, uid):
        return frozenset(self.table.get(uid, ()))


def _token(sub, key=KEY, **over):
    now = int(time.time())
    claims = {"sub": str(sub), "iss": f"{URL}/auth/v1", "aud": "authenticated", "iat": now, "exp": now + 300, **over}
    return jwt.encode(claims, key, algorithm="ES256")


VIEWER, STRANGER = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def client():
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}))
    return TestClient(app)


def _get(client, path, token=None):
    return client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {})


def test_health_is_public_and_shows_shadow(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["mode"] == "SHADOW" and r.json()["live_enabled"] is False


def test_allowlisted_viewer_reads_overview(client):
    r = _get(client, "/api/v1/overview", _token(VIEWER))
    assert r.status_code == 200 and r.headers["x-request-id"]
    assert _get(client, "/api/v1/me", _token(VIEWER)).json()["roles"] == ["viewer"]


@pytest.mark.parametrize("token,status,code", [
    (None, 401, "missing_token"),
    ("not-a-jwt", 401, "invalid_token"),
])
def test_missing_or_garbage_token(client, token, status, code):
    r = _get(client, "/api/v1/overview", token)
    assert r.status_code == status and r.json()["code"] == code and r.json()["request_id"]


def test_rejects_wrong_signature_issuer_audience_and_expiry(client):
    for bad in (_token(VIEWER, key=OTHER), _token(VIEWER, iss="https://evil/auth/v1"),
                _token(VIEWER, aud="anon"), _token(VIEWER, exp=int(time.time()) - 10)):
        assert _get(client, "/api/v1/overview", bad).status_code == 401


def test_valid_token_without_allowlist_is_forbidden(client):
    r = _get(client, "/api/v1/overview", _token(STRANGER))
    assert r.status_code == 403 and r.json()["code"] == "not_allowlisted"


def test_role_claim_in_token_is_ignored(client):
    r = _get(client, "/api/v1/overview", _token(STRANGER, role="service_role", app_metadata={"role": "admin"}))
    assert r.status_code == 403


def test_revoked_user_is_refused_even_with_a_valid_token():
    roles = _Roles({VIEWER: {OperatorRole.VIEWER}})
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), roles)
    c, tok = TestClient(app), _token(VIEWER)
    assert _get(c, "/api/v1/overview", tok).status_code == 200
    roles.table[VIEWER] = set()                     # role set active = false
    r = _get(c, "/api/v1/overview", tok)
    assert r.status_code == 403 and r.json()["code"] == "not_allowlisted"


def test_overview_lists_portfolios_with_latest_equity_and_stale_flag():
    from datetime import UTC, datetime, timedelta

    from api.portfolios import overview_items

    class _Store:
        async def overview_rows(self):
            now = datetime.now(UTC)
            return [("pathA-v1.1", "SHADOW", "USDT", "10000", now - timedelta(hours=3), "10012.5", "10012.5", "10020", "ok"),
                    ("old-book", "SHADOW", "USDT", "10000", now - timedelta(days=3), "9900", "9890", "10000", "ok"),
                    ("new-book", "SHADOW", "USDT", "10000", None, None, None, None, None)]

    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), _Store())
    body = _get(TestClient(app), "/api/v1/overview", _token(VIEWER)).json()
    assert [p["label"] for p in body["portfolios"]] == ["pathA-v1.1", "old-book", "new-book"]
    assert [p["stale"] for p in body["portfolios"]] == [False, True, True]
    assert body["portfolios"][0]["economic_equity"] == "10012.5"           # decimal strings, not floats
    assert overview_items([]) == []


def test_overview_rows_need_a_viewer(client):
    assert _get(client, "/api/v1/overview").status_code == 401
    assert _get(client, "/api/v1/overview", _token(STRANGER)).status_code == 403
