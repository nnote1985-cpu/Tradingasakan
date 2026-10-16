import time
import uuid
from decimal import Decimal

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


def test_ai_scorecard_judges_each_answer_by_the_later_btc_close():
    from datetime import UTC, datetime

    from api.ai import scorecard
    marks = [(datetime(2026, 10, d, 23, 59, tzinfo=UTC), "1", "1", m, "0", "ok") for d, m in [(14, "70000"), (15, "63000"), (16, "63000")]]
    items = [{"proposal_id": f"p{i}", "kind": k, "decided_on": day, "outcome": o}
             for i, (k, day, o) in enumerate([("filter", "2026-10-14", "VETO"), ("filter", "2026-10-14", "ALLOW"),
                                              ("filter", "2026-10-14", "TIMEOUT"), ("filter", "2026-10-15", "ALLOW"),
                                              ("diagnostic", "2026-10-16", "VETO")])]
    s = scorecard(items, marks, horizon=1)
    assert [i["verdict"] for i in s["items"]] == ["avoided_loss", "allowed_loss", "avoided_loss", "flat", "pending"]
    assert s["items"][0]["forward_return"] == "-0.100000" and s["items"][4]["forward_return"] is None
    assert s["counts"] == {"filter": {"avoided_loss": 2, "allowed_loss": 1, "flat": 1}, "diagnostic": {"pending": 1}}
    assert s["diagnostic_only"] is True
    up = [(datetime(2026, 10, d, tzinfo=UTC), "1", "1", m, "0", "ok") for d, m in [(14, "100"), (15, "110")]]
    held, allowed = scorecard([items[0], items[1]], up, horizon=1)["items"]
    assert (held["verdict"], allowed["verdict"]) == ("missed_gain", "allowed_gain")


def test_ai_settings_and_scorecard_endpoints():
    c = _p5_client()
    s = _get(c, "/api/v1/ai/settings", _token(VIEWER)).json()
    assert s["locked"] is True and s["capability_stage"] == "FILTER_ONLY" and s["model_id"] == "claude-opus-5-5"
    assert s["monthly_cap_usd"] == "5" and s["timebox_days"] == 28
    assert _get(c, "/api/v1/ai/settings", _token(STRANGER)).status_code == 403
    sc = _get(c, "/api/v1/portfolios/pathA-p5-r1/ai/ai-scorecard", _token(VIEWER)).json()
    assert sc["available"] and sc["horizon_days"] == 7 and sc["diagnostic_only"] is True
    assert [i["verdict"] for i in sc["items"]] == ["pending", "pending", "pending"]   # 7 days have not passed yet
    assert _get(c, "/api/v1/portfolios/nope/ai-scorecard", _token(VIEWER)).status_code == 404
    gone = _get(_p5_client(missing=True), "/api/v1/portfolios/pathA-p5-r1/ai/ai-scorecard", _token(VIEWER)).json()
    assert gone["available"] is False and gone["items"] == []


def test_backtests_list_r7_with_drawdowns_and_need_a_viewer():
    c = _p5_client()
    body = _get(c, "/api/v1/backtests?market=futures", _token(VIEWER)).json()
    r7 = next(i for i in body["items"] if i["id"] == "R7")
    assert r7["status"] == "dev_passed_unconfirmed" and r7["market"] == "futures"
    assert r7["points"][0] == {"day": "2022-04-11", "strategy": "10000.00", "buy_and_hold": "10000.00",
                               "strategy_drawdown": "0.000000", "buy_and_hold_drawdown": "0.000000"}
    assert max(p["strategy_drawdown"] for p in r7["points"]) == r7["summary"]["max_dd"]
    trades = r7["trades"]
    assert len(trades) == r7["summary"]["flips"] == r7["trade_stats"]["count"] and "fills" not in r7
    assert trades[0] == {"n": 1, "side": "short", "entry_day": "2022-04-12", "entry_price": "39497.70", "qty": "0.25317931",
                         "exit_day": "2022-04-20", "exit_price": "41481.19", "open": False, "days": 8,
                         "pnl": "-513.09", "return": "-0.051309"}
    assert trades[-1]["open"] is True and trades[-1]["exit_day"] is None
    # every trade's P&L includes its fees and funding, so together they give the run's net
    assert sum(Decimal(t["pnl"]) for t in trades) == Decimal(r7["points"][-1]["strategy"]) - Decimal(r7["initial_equity"])
    assert Decimal(r7["trade_stats"]["long_pnl"]) + Decimal(r7["trade_stats"]["short_pnl"]) == sum(Decimal(t["pnl"]) for t in trades)
    assert _get(c, "/api/v1/backtests?market=options", _token(VIEWER)).json() == {"items": []}
    assert _get(c, "/api/v1/backtests", _token(STRANGER)).status_code == 403


def test_backtest_drawdowns_follow_the_running_peak(tmp_path):
    from api.backtests import load_backtests
    (tmp_path / "x.json").write_text('{"id": "X", "market": "futures", "points": ['
                                     '{"day": "a", "strategy": "100", "buy_and_hold": "100"},'
                                     '{"day": "b", "strategy": "120", "buy_and_hold": "90"},'
                                     '{"day": "c", "strategy": "90", "buy_and_hold": "99"}]}')
    [x] = load_backtests("futures", tmp_path)
    assert [p["strategy_drawdown"] for p in x["points"]] == ["0.000000", "0.000000", "0.250000"]
    assert [p["buy_and_hold_drawdown"] for p in x["points"]] == ["0.000000", "0.100000", "0.010000"]
    assert load_backtests("spot", tmp_path) == []


def test_round_trips_pair_legs_and_mark_an_open_trade():
    from api.backtests import round_trips, trade_stats
    fills = [{"day": "2024-01-01", "action": "open", "side": "long", "price": "100", "qty": "1", "equity": "99.9"},
             {"day": "2024-01-05", "action": "close", "side": "long", "price": "110", "qty": "1", "equity": "109.8"},
             {"day": "2024-01-05", "action": "open", "side": "short", "price": "110", "qty": "1", "equity": "109.7"}]
    t = round_trips(fills, Decimal("100"), "2024-01-10", Decimal("104.8"))
    assert [(x["side"], x["days"], x["pnl"], x["return"], x["open"]) for x in t] == [
        ("long", 4, "9.80", "0.098000", False), ("short", 5, "-5.00", "-0.045537", True)]
    assert trade_stats(t) == {"count": 2, "wins": 1, "long_pnl": "9.80", "short_pnl": "-5.00"}
