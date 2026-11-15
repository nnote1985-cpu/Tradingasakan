"""M2 PR 4: bot overlays (docs/m2-charts-plan.md §5.3 O1–O3), on rows shaped like the soak's in Supabase."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from test_api_auth import STRANGER, URL, VIEWER, _get, _Jwks, _Roles, _token

from api.auth import SupabaseJwtVerifier
from api.main import create_app
from api.overlays import overlay_items
from trading.config import Settings
from trading.contracts.enums import OperatorRole


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


BOOK = "tn-r10a-code-v1"
FILLS = [   # exec_trades ⨝ exec_intents for book tn-r10a-code-v1 (conformance trades have another book)
    (_at("2026-10-19 08:26:07.335"), "BUY", Decimal("0.024110000000000000"), Decimal("82786.010000000000000000"),
     Decimal("0"), "BTC", "tak-8e196904", "r10a-2026-10-18", "TESTNET"),
    (_at("2026-10-20 15:40:17.786"), "SELL", Decimal("0.024080000000000000"), Decimal("82569.990000000000000000"),
     Decimal("0"), "USDT", "tak-2702b14d", "halt-e2", "TESTNET"),
]
DECISIONS = [
    (date(2026, 10, 18), "tn-r10a-code-v1", "OPEN", Decimal("1.000000"), Decimal("0"), "close 82,078.00 ...", "a" * 64,
     _at("2026-10-19 08:25:50"), "tak-8e196904", "FILLED"),
    (date(2026, 10, 29), "tn-r10a-code-v1", "NO_DECISION", None, Decimal("1.000000"), "ราคาย้อนหลัง 100 วันไม่ครบ", "b" * 64,
     _at("2026-10-30 08:40:00"), None, None),
    (date(2026, 11, 8), "tn-r10a-code-v1.1", "REBALANCE", Decimal("0"), Decimal("1.000000"), "close ...; out", "c" * 64,
     _at("2026-11-09 08:45:00"), None, "BELOW_MIN"),
]
EVENTS = [
    (_at("2026-10-20 15:40:02.877"), "HALT", None, "สั่งโดย claude-for-note (ซ้อม HALT)"),
    (_at("2026-10-20 15:40:27.245"), "FLAT_CONFIRMED", None, None),
    (_at("2026-10-20 15:43:31.877"), "RESUME", None, "drill 1 done"),
    (_at("2026-10-31 01:32:40.399"), "HALT", None, "drill 2/4/5"),
    (_at("2026-10-31 01:32:43.113"), "FLAT_CONFIRMED", None, None),
    (_at("2026-10-31 04:37:07.951"), "RESUME", None, "drills done"),
    (_at("2026-11-05 09:00:00"), "DAY_LOSS_ALERT", BOOK, "day loss 5%"),
]


def test_fills_are_the_books_trades_with_their_decision():
    o = overlay_items(FILLS, DECISIONS, EVENTS, _at("2026-10-01 00:00"))
    assert o["mode"] == "TESTNET"
    buy, sell = o["fills"]
    assert (buy["side"], buy["qty"], buy["price"], buy["kind"], buy["decision_id"]) == ("BUY", "0.02411", "82786.01", "strategy", "r10a-2026-10-18")
    assert buy["at"] == "2026-10-19T08:26:07.335000+00:00"
    assert (sell["side"], sell["qty"], sell["price"], sell["kind"]) == ("SELL", "0.02408", "82569.99", "halt")


def test_every_decision_is_a_marker_with_action_weight_and_notes():
    o = overlay_items(FILLS, DECISIONS, EVENTS, _at("2026-10-01 00:00"))
    d = o["decisions"]
    assert [x["action"] for x in d] == ["OPEN", "NO_DECISION", "REBALANCE"]
    assert d[0]["filled"] and d[0]["weight"] == "1" and d[0]["previous_weight"] == "0"
    assert d[1]["weight"] is None and d[1]["notes"] == "ราคาย้อนหลัง 100 วันไม่ครบ" and not d[1]["filled"]
    assert d[2]["outcome"] == "BELOW_MIN" and d[2]["t"] == int(_at("2026-11-08 00:00").timestamp())


def test_halts_are_periods_and_alerts_are_points():
    o = overlay_items(FILLS, DECISIONS, EVENTS, _at("2026-10-01 00:00"))
    assert [(h["from"][:19], h["to"][:19], h["flat_at"][:19]) for h in o["halts"]] == [
        ("2026-10-20T15:40:02", "2026-10-20T15:43:31", "2026-10-20T15:40:27"),
        ("2026-10-31T01:32:40", "2026-10-31T04:37:07", "2026-10-31T01:32:43")]
    assert o["halts"][0]["reason"].startswith("สั่งโดย")
    assert [a["kind"] for a in o["alerts"]] == ["DAY_LOSS_ALERT"]
    # a HALT still in force has no end; a period that ended before `from` is left out
    later = overlay_items([], [], [*EVENTS, (_at("2026-11-14 10:00"), "HALT", None, "x")], _at("2026-11-01 00:00"))
    assert [(h["to"], h["reason"]) for h in later["halts"]] == [(None, "x")]


class _Store:
    async def rows(self, account, book, start, end):
        if (account, book) != ("DEFAULT", BOOK):
            return [], [], []
        return FILLS, DECISIONS, EVENTS


def test_overlay_endpoint():
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), overlays=_Store())
    c = TestClient(app)
    r = _get(c, f"/api/v1/bots/DEFAULT/{BOOK}/overlays?from=2026-10-01T00:00:00Z", _token(VIEWER))
    assert r.status_code == 200 and r.json()["available"] and len(r.json()["fills"]) == 2
    assert _get(c, f"/api/v1/bots/DEFAULT/{BOOK}/overlays?from=2026-11-01T00:00:00Z&to=2026-10-01T00:00:00Z", _token(VIEWER)).status_code == 422
    assert _get(c, f"/api/v1/bots/DEFAULT/{BOOK}/overlays", _token(STRANGER)).status_code == 403
    bare = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}))
    assert _get(TestClient(bare), f"/api/v1/bots/DEFAULT/{BOOK}/overlays", _token(VIEWER)).json()["available"] is False
