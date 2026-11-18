from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from api.paper_compare import compare_view, positions

NOW = datetime(2026, 11, 18, 11, 0, tzinfo=UTC)


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T23:59:59.999+00:00")


SNAPSHOTS = [
    # R10a: running since 10-15, flat in cash on 11-16 and 11-17
    ("pathA-r10a/paper", "10000", _at("2026-11-15"), "8500", "70000"),
    ("pathA-r10a/paper", "10000", _at("2026-11-16"), "8400", "66000"),
    ("pathA-r10a/paper", "10000", _at("2026-11-17"), "8388.40", "64947.35"),
    ("pathA-r10a/paper", "10000", _at("2026-11-18"), "8388.40", "60000"),
    ("pathA-r10a/buy_and_hold", "10000", _at("2026-11-15"), "8500", "70000"),
    ("pathA-r10a/buy_and_hold", "10000", _at("2026-11-17"), "7938", "64947.35"),
    ("pathA-r10a/buy_and_hold", "10000", _at("2026-11-18"), "7300", "60000"),
    # 3a: opened 11-17
    ("pathA-r10a-hedge/paper", "10000", _at("2026-11-17"), "9990.642847578911", "64947.35"),
    ("pathA-r10a-hedge/paper", "10000", _at("2026-11-18"), "10010", "60000"),
    ("pathA-r10a-hedge/buy_and_hold", "10000", _at("2026-11-17"), "10171.69", "64947.35"),
    ("pathA-r10a-hedge/buy_and_hold", "10000", _at("2026-11-18"), "9400", "60000"),
    # ended book: left out
    ("pathA-v1/paper", "10000", _at("2026-10-14"), "10000", "80000"),
]
TOTALS = [
    ("pathA-r10a-hedge/paper", "fees", "7.500499999941"),
    ("pathA-r10a-hedge/paper", "funding", "-0.490496551943351811"),
    ("pathA-r10a/paper", "fees", "18.396799478613"),
]
FILLS = [
    ("pathA-r10a/paper", "pathA-r10a/paper:fill:2026-10-16:decision", "BUY 0.122217525601 BTC @ 81739.504632"),
    ("pathA-r10a/paper", "pathA-r10a/paper:fill:2026-11-12:decision", "SELL 0.122217525601 BTC @ 68703.726506"),
    ("pathA-r10a/buy_and_hold", "pathA-r10a/buy_and_hold:fill:buy_and_hold", "BUY 0.122217525601 BTC @ 81739.504632"),
    ("pathA-r10a-hedge/paper", "pathA-r10a-hedge/paper:perp:fill:2026-11-16:decision:sell", "SELL 0.078438385863 BTC @ 63731.55114"),
    ("pathA-r10a-hedge/paper", "pathA-r10a-hedge/paper:spot:fill:2026-11-16:decision:buy", "BUY 0.078401217288 BTC @ 63787.274904"),
    ("futures-r7/paper", "futures-r7/paper:fill:2026-11-10:decision:open", "SELL 0.120725222201 BTC @ 73546.58774"),
    ("pathA-r10a-hedge/paper", "pathA-r10a-hedge/paper:spot:open", "starting cash"),
]


def _book(view, label):
    return next(b for b in view["books"] if b["label"] == label)


def test_positions_split_spot_and_perp_legs():
    p = positions(FILLS)
    assert p["pathA-r10a-hedge/paper"] == {"spot": Decimal("0.078401217288"), "perp": Decimal("-0.078438385863")}
    assert p["pathA-r10a/paper"] == {"spot": Decimal(0), "perp": Decimal(0)}
    assert p["futures-r7/paper"]["perp"] == Decimal("-0.120725222201")


def test_default_start_is_the_3a_open_day_and_books_share_it():
    v = compare_view(SNAPSHOTS, TOTALS, FILLS, now=NOW)
    assert v["from"] == "2026-11-17" and v["default_from"] == "2026-11-17" and v["earliest"] == "2026-11-15"
    # running books + one (the longest) buy & hold; the ended book is left out
    assert [b["label"] for b in v["books"]] == ["pathA-r10a-hedge/paper", "pathA-r10a/paper", "pathA-r10a/buy_and_hold"]

    r10a = _book(v, "pathA-r10a/paper")
    assert [p["day"] for p in r10a["points"]] == ["2026-11-17", "2026-11-18"]      # measured from the 11-17 close
    assert r10a["points"][0]["return"] == "0.000000" and r10a["return"] == "0.000000"
    assert r10a["return_since_open"] == "-0.161160"
    assert r10a["spot_share"] == "0.000000" and r10a["kind"] == "spot" and r10a["fees"] == "18.40"

    hedge = _book(v, "pathA-r10a-hedge/paper")                  # opened on the start day: from its starting cash
    assert hedge["kind"] == "hedge" and hedge["opened_on"] == "2026-11-17" and hedge["days"] == 2
    assert hedge["points"][0]["return"] == "-0.000936" and hedge["return"] == "0.001000"
    assert hedge["funding_received"] == "0.49" and hedge["fees"] == "7.50"
    assert hedge["spot_qty"] == "0.078401" and hedge["perp_qty"] == "-0.078438"
    assert hedge["spot_share"] == "0.469937" and hedge["perp_share"] == "-0.470160"   # qty x 60,000 / 10,010

    bh = _book(v, "pathA-r10a/buy_and_hold")
    assert bh["benchmark"] is True
    assert bh["return"] == str((Decimal(7300) / Decimal(7938) - 1).quantize(Decimal("0.000001")))
    assert bh["drawdown"] == bh["max_drawdown"] == str((1 - Decimal(7300) / Decimal(7938)).quantize(Decimal("0.000001")))


def test_earlier_start_measures_each_book_from_its_own_start():
    v = compare_view(SNAPSHOTS, TOTALS, FILLS, start=date(2026, 11, 16), now=NOW)
    r10a = _book(v, "pathA-r10a/paper")                          # from the 11-16 close
    assert len(r10a["points"]) == 3 and r10a["max_drawdown"] == "0.001381"       # 8388.40 / 8400
    assert len(_book(v, "pathA-r10a-hedge/paper")["points"]) == 2
    first = compare_view(SNAPSHOTS, TOTALS, FILLS, start=date(2026, 11, 15), now=NOW)   # its first close: from 10,000
    assert _book(first, "pathA-r10a/paper")["max_drawdown"] == "0.161160"


def test_empty_database():
    v = compare_view([], [], [], now=NOW)
    assert v == {"from": None, "default_from": None, "earliest": None, "focus": "pathA-r10a-hedge", "books": []}


class _Store:
    async def snapshot_rows(self):
        return SNAPSHOTS

    async def total_rows(self):
        return TOTALS

    async def fill_rows(self):
        return FILLS


def test_endpoint_needs_a_viewer_and_takes_from():
    from api.auth import SupabaseJwtVerifier
    from api.main import create_app
    from tests.unit.test_api_auth import VIEWER, _Jwks, _Roles, _token
    from trading.config import Settings
    from trading.contracts.enums import OperatorRole

    app = create_app(Settings(), SupabaseJwtVerifier("https://example.supabase.co", jwks_client=_Jwks()),
                     _Roles({VIEWER: {OperatorRole.VIEWER}}), compare=_Store())
    c = TestClient(app)
    assert c.get("/api/v1/paper-compare").status_code == 401
    r = c.get("/api/v1/paper-compare?from=2026-11-15", headers={"Authorization": f"Bearer {_token(VIEWER)}"})
    assert r.status_code == 200 and r.json()["from"] == "2026-11-15"
    # stale books drop out against the real clock; the shape is what matters here
    assert set(r.json()) == {"from", "default_from", "earliest", "focus", "books"}


def test_spot_buy_and_hold_is_preferred_over_a_longer_perp_one():
    perp_bh = [("futures-r7/buy_and_hold", "10000", _at("2026-11-14"), "8000", "70000")]
    v = compare_view(SNAPSHOTS + perp_bh, TOTALS, FILLS, now=NOW)
    assert [b["label"] for b in v["books"] if b["benchmark"]] == ["pathA-r10a/buy_and_hold"]
