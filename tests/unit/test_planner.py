"""M3 A1/A2: the strategy contract and the shared order planner (docs/m3-strategy-core-plan.md §4.2, §4.3)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from trading.contracts.strategy import Market, Strategy, StrategyInputs, TargetDecision, inputs_hash
from trading.execution import planner as pl
from trading.execution.planner import Leg, off_target, plan_perp, plan_spot, spot_equity
from trading.execution.rules import TradingRules
from trading.execution.safety import SafetyState
from trading.execution.types import Side

# BTCUSDT on the Spot Testnet (step 0.00001, min notional 5); the perp numbers are only for shape.
RULES = TradingRules("BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"), min_qty=D("0.00001"),
                     max_qty=D("9000"), min_notional=D("5"))
PERP = TradingRules("BTCUSDT", tick_size=D("0.1"), step_size=D("0.001"), min_qty=D("0.001"),
                    max_qty=D("1000"), min_notional=D("100"))
CAP = D("2000")
NOW = datetime(2026, 11, 15, 1, tzinfo=UTC)


def spot(w, held="0", cash="0", bid="80000", ask="80001", alloc="2000", **kw):
    return plan_spot(D(w), allocation=D(alloc), cash=D(cash), held=D(held), bid=D(bid), ask=D(ask), rules=RULES,
                     max_order_notional=CAP, **kw)


def perp(w, pos="0", equity="2000", bid="80000", ask="80000", **kw):
    return plan_perp(D(w), equity=D(equity), position=D(pos), bid=D(bid), ask=D(ask), rules=PERP,
                     max_order_notional=kw.pop("cap", CAP), **kw)


# --- contract ------------------------------------------------------------------------------------

def _decision(**kw):
    base = dict(strategy_version="tn-x-v1", instrument="BTCUSDT", market=Market.SPOT, day=date(2026, 11, 14),
                target_weight=D("0.5"), reason="test", inputs={"a": 1.5, "b": [1, 2]}, data_ready_at=NOW)
    base.update(kw)
    return TargetDecision.make(**base)


def test_decision_hash_is_the_stored_snapshot_hash():
    d = _decision()
    assert d.inputs_sha256 == inputs_hash({"b": [1, 2], "a": 1.5})        # key order does not matter
    with pytest.raises(ValueError, match="does not match"):
        TargetDecision(**{**d.__dict__, "inputs_sha256": "0" * 64})


@pytest.mark.parametrize("w", [D("1.01"), D("-1.5"), D("NaN"), D("Infinity")])
def test_decision_weight_must_be_finite_and_within_one(w):
    with pytest.raises(ValueError):
        _decision(target_weight=w)


def test_decision_allows_short_and_no_decision():
    assert _decision(market=Market.PERP, target_weight=D("-1")).target_weight == D(-1)
    assert _decision(target_weight=None).target_weight is None
    with pytest.raises(ValueError, match="timezone"):
        _decision(data_ready_at=datetime(2026, 11, 15))


def test_strategy_protocol_is_structural():
    class S:
        version, instrument, market = "s", "BTCUSDT", Market.SPOT

        def load(self, day):
            return StrategyInputs(bars=None)

        def decide(self, inputs, day, data_ready_at):
            return None

    assert isinstance(S(), Strategy)


# --- spot ----------------------------------------------------------------------------------------

def test_spot_negative_weight_is_refused_in_the_planner():
    p = spot("-0.5", held="0.01")
    assert p.empty and p.refused and p.reason == pl.SPOT_NEGATIVE_WEIGHT


@pytest.mark.parametrize("w", ["1.5", "-2"])
def test_weight_out_of_range_is_refused(w):
    assert spot(w).reason == pl.WEIGHT_OUT_OF_RANGE and perp(w).reason == pl.WEIGHT_OUT_OF_RANGE


def test_spot_open_rounds_down_to_the_step_under_the_cap():
    p = spot("1", ask="82786.01", bid="82786")
    (leg,) = p.legs
    # 2000 is capped at 1998 (99.9% of the order cap), the fee is reserved, then down to 0.00001
    assert leg == Leg(Side.BUY, D("0.02411"), D("82786.01"), reduce_only=False)
    assert leg.notional <= D("1998")


def test_spot_rounding_never_sends_more_than_planned():
    for ask in ("12345.67", "99999.99", "67890.13"):
        (leg,) = spot("0.5", ask=ask, bid=ask).legs
        assert leg.qty % RULES.step_size == 0
        assert leg.qty * D(ask) * (1 + pl.FEE_ALLOWANCE) <= D("1000")


def test_spot_flat_sells_everything_held():
    (leg,) = spot("0", held="0.02408589", cash="-1996").legs
    assert leg == Leg(Side.SELL, D("0.02408"), D("80000"), reduce_only=True)


def test_spot_partial_decrease_and_increase():
    # 0.02 BTC at 80,000 = 1,600 held, 400 cash: equity 2,000. Half = 1,000 -> sell 600 worth.
    (sell,) = spot("0.5", held="0.02", cash="-1600").legs
    assert sell.side is Side.SELL and sell.qty == D("0.0075") and sell.reduce_only
    # 0.005 BTC = 400 held, 1,600 cash: half -> buy 600 worth (fee reserved, rounded down)
    (buy,) = spot("0.5", held="0.005", cash="-400").legs
    assert buy.side is Side.BUY and buy.qty == D("0.00749") and not buy.reduce_only


def test_spot_difference_below_the_minimum_sends_nothing():
    p = spot("0", held="0.00000589")                       # the 11-08 case: dust after the HALT drill sold
    assert p.empty and p.reason == pl.BELOW_MIN and not p.refused
    assert spot("1", held="0.025", cash="-1998").reason == pl.BELOW_MIN   # 2 USDT short of the target


def test_spot_insufficient_funds():
    # The exchange account has 3 USDT free although the book thinks it has 2,000.
    p = spot("1", free_quote=D("3"))
    assert p.empty and p.reason == pl.INSUFFICIENT_FUNDS
    (leg,) = spot("1", free_quote=D("500")).legs                # a smaller buy when some cash is free
    assert leg.notional <= D("500")


def test_spot_never_sells_more_than_held():
    (leg,) = spot("0.2", held="0.001", cash="5000").legs       # cash says buy; holdings are small
    assert leg.side is Side.BUY
    (leg,) = spot("0", held="-0.001").legs                     # a negative holding is never sold (bought back, as v1.1)
    assert leg.side is Side.BUY


def test_off_target_matches_v11():
    eq = spot_equity(D("2000"), D("-1880"), D("0.0235"), D("80000"))
    assert eq == D("2000")
    assert not off_target(D("1"), D("0.0235"), eq, D("80000"), D("5"))      # 120 off, threshold 10% = 200
    assert off_target(D("1"), D("0.0175"), eq, D("80000"), D("5"))          # 600 off


# --- perp ----------------------------------------------------------------------------------------

def test_perp_open_long_and_short():
    (leg,) = perp("1").legs
    assert leg.side is Side.BUY and not leg.reduce_only and leg.qty == D("0.024")
    (leg,) = perp("-1").legs
    assert leg.side is Side.SELL and not leg.reduce_only and leg.qty == D("0.024")


def test_perp_partial_increase_and_decrease():
    (inc,) = perp("1", pos="0.01", equity="4000", cap=D("10000")).legs         # 0.01 -> 0.05
    assert inc.side is Side.BUY and not inc.reduce_only and inc.qty == D("0.039")
    (dec,) = perp("0.5", pos="0.05", equity="4000").legs                        # 0.05 -> 0.025
    assert dec.side is Side.SELL and dec.reduce_only and dec.qty == D("0.025")
    (dec,) = perp("-0.5", pos="-0.05", equity="4000").legs                      # short 0.05 -> 0.025
    assert dec.side is Side.BUY and dec.reduce_only and dec.qty == D("0.025")


def test_perp_close_all():
    (leg,) = perp("0", pos="-0.024").legs
    assert leg == Leg(Side.BUY, D("0.024"), D("80000"), reduce_only=True)


def test_perp_side_flip_is_close_then_open():
    p = perp("-1", pos="0.024", equity="2000")
    assert [(x.side, x.reduce_only) for x in p.legs] == [(Side.SELL, True), (Side.SELL, False)]
    assert p.legs[0].qty == D("0.024") and p.legs[1].qty == D("0.024") and p.reason is None


def test_perp_flip_with_a_small_new_side_sends_only_the_close():
    p = perp("-0.04", pos="0.024", equity="2000")                     # new side 80 USDT < min notional 100
    assert [(x.side, x.reduce_only) for x in p.legs] == [(Side.SELL, True)] and p.reason == pl.OPEN_LEG_BELOW_MIN


def test_perp_below_min_and_insufficient_margin():
    assert perp("1", pos="0.0245", equity="2000").reason == pl.BELOW_MIN       # 0.0005 more < step
    assert perp("1", free_margin=D("50")).reason == pl.INSUFFICIENT_FUNDS
    assert perp("0", pos="0.0004").reason == pl.BELOW_MIN


def test_no_quote():
    assert spot("1", bid="0").reason == pl.NO_QUOTE and perp("1", ask="0").reason == pl.NO_QUOTE


# --- I3: the planner sends what tn-r10a-code-v1.1 sends ---------------------------------------------

class _Store:
    def __init__(self, held: D, cash: D) -> None:
        self.pos, self.intent, self.finished = (held, cash), None, None

    def position(self, *_a):
        return self.pos

    def accept_intent(self, intent, **_kw):
        self.intent = intent
        return True

    def decision_attempt(self, *_a):
        pass

    def finish_decision(self, token, now, book, day, outcome):
        self.finished = outcome


def _v11(weight: D, held: D, cash: D, bid: D, ask: D):
    """Run the real ``R10aBot._execute`` of v1.1 on fakes and return (side, qty, reduce_only) or None."""
    from trading.runtime.r10a import R10aBot

    store = _Store(held, cash)
    bot = R10aBot(store, fetch=lambda d: None)  # type: ignore[arg-type]
    ex = SimpleNamespace(safety=SimpleNamespace(state=SafetyState.RUNNING, allows_entries=True, epoch=1),
                         rules=RULES, orders={}, waiting={}, now=lambda: NOW, quote=lambda: SimpleNamespace(bid=bid, ask=ask),
                         limits=SimpleNamespace(max_order_notional=CAP), alerts=SimpleNamespace(push=lambda *a: None))
    row = {"day": date(2026, 11, 14), "client_order_id": None, "attempts": 0, "weight": str(weight)}
    bot._execute(ex, 1, row)  # type: ignore[arg-type]
    bot.close()
    i = store.intent
    return None if i is None else (i.side, i.qty, i.reduce_only)


def test_planner_matches_v11_on_random_states():
    rnd = random.Random(20261115)
    n = 0
    for _ in range(5000):
        weight = rnd.choice([D(0), D("0.5"), D(1)])
        bid = D(rnd.randint(2000000, 12000000)) / 100
        ask = bid + D(rnd.randint(1, 500)) / 100
        held = rnd.choice([D(0), D("0.00000589"), D(rnd.randint(0, 6000000)) / 10**8])
        # cash change of a book that bought ``held`` somewhere between 50% and 150% of today's price
        cash = -(held * bid * D(rnd.randint(50, 150)) / 100).quantize(D("0.01"))
        if spot_equity(D(2000), cash, held, bid) <= 0:
            continue
        want = _v11(weight, held, cash, bid, ask)
        p = plan_spot(weight, allocation=D(2000), cash=cash, held=held, bid=bid, ask=ask, rules=RULES, max_order_notional=CAP)
        got = None if p.empty else (p.legs[0].side, p.legs[0].qty, p.legs[0].reduce_only)
        assert got == want, (weight, held, cash, bid, ask)
        n += want is not None
    assert n > 2000                                   # most states trade, so the sizes were really compared


def test_soak_orders_i1_i2():
    """The soak's only strategy order (10-18) and the 11-08 rebalance after the HALT drill had sold."""
    (buy,) = spot("1", bid="82786.00", ask="82786.01").legs
    assert (buy.side, buy.qty) == (Side.BUY, D("0.02411"))
    assert _v11(D(1), D(0), D(0), D("82786.00"), D("82786.01")) == (Side.BUY, D("0.02411"), False)
    # 0.02411 bought (fee 0.00002411 BTC), Guardian sold 0.02408 at 82,569.99 (fee 1.98828536 USDT)
    held = D("0.02411") - D("0.00002411") - D("0.02408")
    cash = -D("0.02411") * D("82786.01") + D("0.02408") * D("82569.99") - D("1.98828536")
    p = spot("0", held=str(held), cash=str(cash), bid="74800", ask="74800.01")
    assert p.empty and p.reason == pl.BELOW_MIN
    assert _v11(D(0), held, cash, D("74800"), D("74800.01")) is None
