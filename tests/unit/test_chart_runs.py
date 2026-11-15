"""M2 PR 6 (E4): backtest and shadow runs on the chart, each with its own mode label."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from test_api_auth import URL, VIEWER, _get, _Jwks, _Roles, _token

from api.auth import SupabaseJwtVerifier
from api.backtests import load_backtests
from api.main import create_app
from api.portfolios import overview_items
from api.runs import backtest_run, run_list, shadow_run
from trading.config import Settings
from trading.contracts.enums import OperatorRole

RESULTS = Path(__file__).resolve().parents[2] / "research" / "results"


def _at(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


PORTFOLIO = ("pathA-r10a/paper", "SHADOW", "USDT", "10000")
ROWS = [  # at, trading, economic, mark, exposure, quality
    (_at("2026-10-17 00:00"), "10000", "10000", "83000", "10000", "OK"),
    (_at("2026-10-18 00:00"), "10100", "10100", "84000", "10100", "OK"),
    (_at("2026-10-19 00:00"), "9900", "9900", "82000", "0", "OK"),
    (_at("2026-10-20 00:00"), "9900", "9900", "81000", "0", "OK"),
]


def test_backtest_run_has_trades_equity_and_drawdown():
    b = [x for x in load_backtests(results_dir=RESULTS) if x["id"] == "R7"][0]
    r = backtest_run(b)
    assert r["mode"] == "BACKTEST" and r["venue"] == "perp" and r["range"] == b["range"]
    assert len(r["equity"]) == len(b["points"]) == len(r["drawdown"])
    first = r["events"][0]
    assert (first["side"], first["action"], first["price"]) == ("SELL", "open", "39497.70")   # R7 opened short on 2022-04-12
    assert first["t"] == int(_at("2022-04-12 00:00").timestamp())
    assert sum(e["action"] == "open" for e in r["events"]) == len(b["trades"])


def test_shadow_run_marks_the_days_exposure_changed():
    r = shadow_run(PORTFOLIO, ROWS)
    assert r["mode"] == "SHADOW" and r["venue"] == "spot" and r["range"] == ["2026-10-17", "2026-10-20"]
    assert [(e["side"], e["text"]) for e in r["events"]] == [("SELL", "ถือ 100% → 0%")]
    assert r["events"][0]["t"] == int(_at("2026-10-19 00:00").timestamp())
    assert r["drawdown"][2]["v"] == "0.019802"


def test_run_list_keeps_modes_apart_and_skips_benchmarks():
    rows = [(*PORTFOLIO, _at("2026-10-20 00:00"), "9900", "9900", None, "OK", "10100", "0"),
            ("pathA-r10a/buy_and_hold", "SHADOW", "USDT", "10000", _at("2026-10-20 00:00"), "9800", "9800", None, "OK", "10000", None),
            ("spot-e1-v1/paper", "SHADOW", "USDT", "10000", None, None, None, None, None, None, None)]
    items = run_list(load_backtests(results_dir=RESULTS), overview_items(rows))
    assert [(i["kind"], i["id"], i["mode"]) for i in items] == [("backtest", "R7", "BACKTEST"), ("shadow", "pathA-r10a/paper", "SHADOW")]


class _Store:
    async def overview_rows(self):
        return [(*PORTFOLIO, _at("2026-10-20 00:00"), "9900", "9900", None, "OK", "10100", "0")]

    async def portfolio(self, label):
        return PORTFOLIO if label == PORTFOLIO[0] else None

    async def equity_rows(self, label):
        return ROWS


def test_run_endpoints():
    app = create_app(Settings(), SupabaseJwtVerifier(URL, jwks_client=_Jwks()), _Roles({VIEWER: {OperatorRole.VIEWER}}), portfolios=_Store())
    c = TestClient(app)
    assert {i["id"] for i in _get(c, "/api/v1/charts/runs", _token(VIEWER)).json()["items"]} == {"R7", "pathA-r10a/paper"}
    assert _get(c, "/api/v1/charts/runs/shadow/pathA-r10a/paper", _token(VIEWER)).json()["mode"] == "SHADOW"
    assert _get(c, "/api/v1/charts/runs/backtest/R7", _token(VIEWER)).json()["mode"] == "BACKTEST"
    assert _get(c, "/api/v1/charts/runs/backtest/R99", _token(VIEWER)).status_code == 404
    assert _get(c, "/api/v1/charts/runs/live/x", _token(VIEWER)).status_code == 422
