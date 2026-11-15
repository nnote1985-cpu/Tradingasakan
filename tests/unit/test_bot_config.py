"""bot-config-v1 validation and hash (docs/m4-bot-config-plan.md §3.1, G5)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from trading.bots import config, templates
from trading.bots.config import AccountFunds, ConfigInvalid

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


def body(**over):
    b = {"name": "R10a v2 BOT1", "account": "BOT1", "venue": "SPOT_TESTNET",
         "instrument": {"symbol": "BTCUSDT", "market": "SPOT"},
         "strategy": {"template": "tn-r10a-code-v2", "version": "tn-r10a-code-v2", "timeframe": "1d", "params": {}},
         "allocation": {"asset": "USDT", "amount": "2000"}, "mode": "SHADOW"}
    b.update(over)
    return b


def fields(payload):
    with pytest.raises(ConfigInvalid) as e:
        config.parse(payload)
    return {x.field: x.code for x in e.value.errors}


def test_example_parses_and_hash_is_pinned():
    c = config.parse(body())
    normalized, h = config.normalize(c)
    assert normalized["allocation"]["amount"] == "2000.00"
    assert normalized["risk_profile"] == "spot.tn.v1" and normalized["futures"] is None
    # Changing normalize changes every stored hash and stops every bot: this value must not move.
    assert h == config.body_hash(normalized)
    assert h == PINNED


PINNED = "bc3532d5d2c5d0dc5145cea56ef0b9fb65ae21f764e008dab33e7901bd7f37da"


def test_hash_ignores_key_order_and_amount_spelling():
    a = config.normalize(config.parse(body()))[1]
    b = config.normalize(config.parse(dict(reversed(list(body(allocation={"amount": "2000.00", "asset": "USDT"}).items())))))[1]
    assert a == b
    assert config.normalize(config.parse(body(mode="TESTNET")))[1] != a


def test_live_and_other_modes_refused():
    assert fields(body(mode="LIVE")) == {"mode": "live_refused"}
    assert fields(body(mode="PAPER")) == {"mode": "invalid"}


def test_unknown_fields_refused():
    assert fields(body(api_key="x")) == {"api_key": "extra_forbidden"}
    assert "strategy.params.length" in fields(body(strategy={**body()["strategy"], "params": {"length": 5}}))


def test_template_version_timeframe_market():
    assert fields(body(strategy={**body()["strategy"], "template": "nope"})) == {"strategy.template": "unknown_template"}
    assert fields(body(strategy={**body()["strategy"], "version": "tn-r10a-code-v1"})) == {"strategy.version": "version_mismatch"}
    assert fields(body(strategy={**body()["strategy"], "timeframe": "4h"})) == {"strategy.timeframe": "timeframe_unsupported"}
    assert fields(body(instrument={"symbol": "ETHUSDT", "market": "SPOT"})) == {"instrument": "template_market"}


def test_venue_must_match_market():
    assert fields(body(venue="SPOT_MAINNET")) == {"venue": "invalid"}


def test_allocation_bounds_and_format():
    assert fields(body(allocation={"asset": "USDT", "amount": "2000.01"})) == {"allocation.amount": "allocation_bounds"}
    assert fields(body(allocation={"asset": "USDT", "amount": "99"})) == {"allocation.amount": "allocation_bounds"}
    assert fields(body(allocation={"asset": "USDT", "amount": "-5"})) == {"allocation.amount": "string_pattern_mismatch"}
    assert fields(body(allocation={"asset": "BTC", "amount": "1"})) == {"allocation.asset": "literal_error"}


def test_account_name_rule():
    assert fields(body(account="BOT 1")) == {"account": "string_pattern_mismatch"}


def perp(**f):
    return body(venue="USDM_TESTNET", instrument={"symbol": "BTCUSDT", "market": "PERP"},
                futures={"leverage": 1, "margin_mode": "isolated", "stop_percent": "25", **f})


def test_futures_forced_1x_isolated_and_stop_inside_liquidation():
    got = fields(perp(leverage=3, margin_mode="cross", stop_percent="99.9"))
    assert got["futures.leverage"] == "leverage_fixed"
    assert got["futures.margin_mode"] == "isolated_only"
    assert got["futures.stop_percent"] == "stop_beyond_liquidation"
    assert fields(perp())["instrument"] == "template_market"       # no Perp template yet
    assert config.liquidation_distance_percent(1) == Decimal("99.6")


def test_spot_refuses_futures_block_and_perp_needs_it():
    assert fields(body(futures={"leverage": 1, "margin_mode": "isolated", "stop_percent": "25"})) == {"futures": "not_allowed"}
    got = fields(body(venue="USDM_TESTNET", instrument={"symbol": "BTCUSDT", "market": "PERP"}))
    assert got["futures"] == "missing"


def test_template_param_bounds():
    p = templates.TemplateParam(default=20, min=5, max=100)
    assert p.error(20) is None and p.error(4) and p.error(20.5) and p.error(True)


def funds(free="5000", age=timedelta(minutes=1), verdict="MATCH"):
    return AccountFunds(Decimal(free), NOW - age, verdict)


def test_approval_checks_free_balance_and_freshness():
    c = config.parse(body())
    assert config.approval_errors(c, funds(), NOW) == []
    assert [e.code for e in config.approval_errors(c, funds(free="1999.99"), NOW)] == ["over_free_balance"]
    assert [e.code for e in config.approval_errors(c, funds(age=timedelta(minutes=16)), NOW)] == ["balance_stale"]
    assert [e.code for e in config.approval_errors(c, funds(verdict="DISCREPANCY"), NOW)] == ["balance_stale"]
    assert [e.code for e in config.approval_errors(c, AccountFunds(None, None, None), NOW)] == ["no_balance"]


def test_book_is_fixed_per_bot():
    assert config.book_for(UUID("12345678-1234-5678-1234-567812345678")) == "bot-12345678"


def test_every_template_builds_a_matching_strategy():
    for t in templates.TEMPLATES.values():
        s = t.factory(t.resolve({}))
        assert (s.version, s.instrument, s.market) == (t.version, t.symbol, t.market)
        assert t.max_allocation <= templates.MAX_ORDER_NOTIONAL
