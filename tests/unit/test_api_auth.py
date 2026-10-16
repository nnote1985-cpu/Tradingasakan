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
            return [("pathA-v1.1", "SHADOW", "USDT", "10000", now - timedelta(hours=3), "10012.5", "10012.5", "10020", "ok", "10020", "5006.25"),
                    ("old-book", "SHADOW", "USDT", "10000", now - timedelta(days=3), "9900", "9890", "10000", "ok", "10000", None),
                    ("new-book", "SHADOW", "USDT", "10000", None, None, None, None, None, "10000", None)]

    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), _Store())
    body = _get(TestClient(app), "/api/v1/overview", _token(VIEWER)).json()
    assert [p["label"] for p in body["portfolios"]] == ["pathA-v1.1", "old-book", "new-book"]
    assert [p["stale"] for p in body["portfolios"]] == [False, True, True]
    assert body["portfolios"][0]["economic_equity"] == "10012.5"           # decimal strings, not floats
    assert [(p["return"], p["drawdown"], p["exposure_share"]) for p in body["portfolios"]] == [
        ("0.001250", "0.000749", "0.500000"), ("-0.011000", "0.011000", None), (None, None, None)]
    assert overview_items([]) == []


def test_overview_rows_need_a_viewer(client):
    assert _get(client, "/api/v1/overview").status_code == 401
    assert _get(client, "/api/v1/overview", _token(STRANGER)).status_code == 403


class _EquityStore:
    """Two days of a book: up 10%, then down to 9,900 (a 10% drawdown from the 11,000 peak)."""
    async def overview_rows(self):
        return []

    async def portfolio(self, label):
        return (label, "SHADOW", "USDT", "10000") if label == "pathA-v1.2/paper" else None

    async def equity_rows(self, label):
        from datetime import UTC, datetime
        return [(datetime(2026, 10, 14, 23, 59, tzinfo=UTC), "11000", "11000", "70000", "11000", "ok"),
                (datetime(2026, 10, 15, 23, 59, tzinfo=UTC), "9900.5", "9900", "63000", "0", "ok")]


def test_equity_history_with_return_and_drawdown_from_the_running_peak():
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), _EquityStore())
    c = TestClient(app)
    body = _get(c, "/api/v1/portfolios/pathA-v1.2/paper/equity", _token(VIEWER)).json()
    assert body["label"] == "pathA-v1.2/paper" and body["mode"] == "SHADOW"
    assert [(p["economic_equity"], p["return"], p["drawdown"]) for p in body["points"]] == [
        ("11000", "0.100000", "0.000000"), ("9900", "-0.010000", "0.100000")]
    assert body["points"][1]["trading_equity"] == "9900.5" and body["max_drawdown"] == "0.100000"
    r = _get(c, "/api/v1/portfolios/nope/equity", _token(VIEWER))
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert _get(c, "/api/v1/portfolios/pathA-v1.2/paper/equity").status_code == 401
    assert _get(c, "/api/v1/portfolios/pathA-v1.2/paper/equity", _token(STRANGER)).status_code == 403


class _P5Store(_EquityStore):
    def __init__(self, missing=False):
        self.missing = missing

    async def portfolio(self, label):
        return (label, "SHADOW", "USDT", "10000") if label.startswith("pathA-") else None

    async def risk_rows(self):
        from datetime import UTC, datetime

        from api.portfolios import Unavailable
        if self.missing:
            raise Unavailable
        return [("pathA-v1.2/paper", True, datetime(2026, 10, 15, 9, tzinfo=UTC), "drawdown 25%"),
                ("pathA-p5-r1/ai", False, None, None)]

    async def decision_rows(self, label):
        from datetime import UTC, date, datetime

        from api.portfolios import Unavailable
        if self.missing:
            raise Unavailable
        at = datetime(2026, 10, 16, 8, 25, tzinfo=UTC)
        return [("p5:2026-10-15:diag", "diagnostic", date(2026, 10, 15), "0.5", "0.5", "VETO", "TREND_WEAK", "r", "SUCCESS", None,
                 "claude-opus-5-5", "0.012000000", 9000, at),
                ("p5:2026-10-15:filter", "filter", date(2026, 10, 15), "0", "0.5", "ALLOW", "TREND_OK", "r", "SUCCESS", None,
                 "claude-opus-5-5", "0.013000000", 8000, at),
                ("p5:2026-10-14:filter", "filter", date(2026, 10, 14), "0", "0.5", "ABSTAIN", "EXPIRED_DECISION", None, "TIMEOUT",
                 "TIMEOUT", "claude-opus-5-5", "0", 120000, at)]


def _p5_client(missing=False):
    return TestClient(create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}),
                                 _P5Store(missing)))


def test_risk_status_and_ai_decisions():
    c = _p5_client()
    r = _get(c, "/api/v1/risk-status", _token(VIEWER)).json()
    assert r["available"] and r["portfolios"][0] == {"label": "pathA-v1.2/paper", "halted": True,
                                                     "halted_since": "2026-10-15T09:00:00+00:00", "halt_reason": "drawdown 25%"}
    d = _get(c, "/api/v1/portfolios/pathA-p5-r1/ai/decisions", _token(VIEWER)).json()
    assert d["available"] and [i["outcome"] for i in d["items"]] == ["VETO", "ALLOW", "TIMEOUT"]
    assert d["counts"] == {"filter": {"ALLOW": 1, "TIMEOUT": 1}, "diagnostic": {"VETO": 1}}   # a timeout is not a VETO
    assert d["cost_usd"] == "0.025000000"
    assert _get(c, "/api/v1/portfolios/nope/decisions", _token(VIEWER)).status_code == 404
    assert _get(c, "/api/v1/risk-status", _token(STRANGER)).status_code == 403


def test_risk_and_decisions_before_the_phase5_migration_say_unavailable():
    c = _p5_client(missing=True)
    assert _get(c, "/api/v1/risk-status", _token(VIEWER)).json() == {"available": False, "portfolios": []}
    assert _get(c, "/api/v1/portfolios/pathA-p5-r1/ai/decisions", _token(VIEWER)).json()["available"] is False
