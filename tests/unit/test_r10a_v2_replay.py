"""M3 §4.1: tn-r10a-code-v2 replays the tn-r10a-code-v1 Testnet soak exactly (R1-R3)."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trading.contracts.strategy import Market, Strategy, TargetDecision
from trading.evaluation.r10a_soak_replay import load_fixture, replay
from trading.execution.rules import TradingRules
from trading.execution.safety import SafetyState
from trading.execution.types import Side
from trading.runtime.target_bot import TargetBot, r10a_v2_bot
from trading.strategies.r10a_v2 import R10aV2

FIXTURE = Path(__file__).parents[1] / "fixtures" / "r10a_soak"
RULES = TradingRules("BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"), min_qty=D("0.00001"),
                     max_qty=D("9000"), min_notional=D("5"))
WAITS = {date(2026, 10, 29)}                # v1 recorded NO_DECISION; v1.1 (and v2) wait for the day's funding


@pytest.fixture(scope="module")
def days():
    rows, k, f = load_fixture(str(FIXTURE))
    assert len(rows) == 18
    return replay(rows, k, f)


def test_r1_v2_equals_v11_on_every_soak_day(days):
    bad = [d.day for d in days if not d.same]
    assert bad == []


def test_r2_v2_reproduces_every_stored_hash_v11_decides(days):
    for d in days:
        if d.day in WAITS:
            assert d.v2 is None and d.v11 is None
            continue
        assert d.matches_stored, d.day
        assert d.v2.reason == d.stored["notes"]
        w = d.stored["weight"]
        assert d.v2.target_weight == (None if w is None else D(str(w)))
        assert d.v2.strategy_version == "tn-r10a-code-v2" and d.v2.market is Market.SPOT
    assert sum(d.matches_stored for d in days) == 17


def test_v2_is_a_strategy():
    assert isinstance(R10aV2(), Strategy)


# --- R3: TargetBot gives the stored actions when the soak's position is replayed --------------------

class SyncPool:
    def submit(self, fn, *args):
        f: Future = Future()
        f.set_result(fn(*args))
        return f

    def shutdown(self, **_kw) -> None:
        pass


class Store:
    """In-memory stand-in for the ExecStore calls TargetBot makes."""

    def __init__(self) -> None:
        self.rows: dict[date, dict[str, Any]] = {}
        self.held, self.cash = D(0), D(0)
        self.intents: list[Any] = []

    def load_decision(self, book, day):
        return self.rows.get(day)

    def last_weight(self, book, before):
        ws = [r["weight"] for d, r in sorted(self.rows.items()) if d < before and r["weight"] is not None]
        return ws[-1] if ws else None

    def add_decision(self, token, now, *, day, weight, action, previous_weight, notes, inputs_sha256, **_kw):
        if day in self.rows:
            return False
        for r in self.rows.values():
            if r["executed_at"] is None:
                r["executed_at"], r["outcome"] = now, f"superseded by {day}"
        self.rows[day] = {"day": day, "weight": weight, "previous_weight": previous_weight, "action": action, "notes": notes,
                          "inputs_sha256": inputs_sha256, "executed_at": None, "outcome": None, "attempts": 0,
                          "client_order_id": None}
        return True

    def position(self, *_a):
        return self.held, self.cash

    def accept_intent(self, intent, *, unit_price, budget, base):
        self.intents.append((intent, unit_price))
        return True

    def decision_attempt(self, token, now, book, day, n, cid):
        self.rows[day].update(attempts=n, client_order_id=cid)

    def finish_decision(self, token, now, book, day, outcome):
        self.rows[day].update(executed_at=now, outcome=outcome)


class Ex:
    def __init__(self, store: Store) -> None:
        self.store, self.t, self.bid, self.ask = store, datetime(2026, 10, 19, 0, 20, tzinfo=UTC), D(0), D(0)
        self.safety = SimpleNamespace(state=SafetyState.RUNNING, allows_entries=True, epoch=1)
        self.rules, self.orders, self.waiting = RULES, {}, {}
        self.limits = SimpleNamespace(max_order_notional=D("2000"))
        self.alerts = SimpleNamespace(push=lambda *a: None)
        self.filled: dict[str, Any] = {}

    def now(self):
        return self.t

    def mono(self):
        return self.t.timestamp()

    def quote(self):
        return SimpleNamespace(bid=self.bid, ask=self.ask)

    def log(self, _m):
        pass

    def lookup(self, cid):
        return self.filled.get(cid)

    def fill(self) -> None:
        """Fill the last accepted intent at its reference price (fee: 0.1% in BTC on buys, in USDT on sells)."""
        intent, price = self.store.intents[-1]
        if intent.side is Side.BUY:
            self.store.held += intent.qty - intent.qty / 1000
            self.store.cash -= intent.qty * price
        else:
            self.store.held -= intent.qty
            self.store.cash += intent.qty * price * D("0.999")
        self.filled[intent.client_order_id] = SimpleNamespace(is_terminal=True, filled_qty=intent.qty, intent=intent,
                                                              state=SimpleNamespace(value="FILLED"))


def test_r3_target_bot_actions_follow_the_soak(days):
    store = Store()
    by_day = {d.day: d.v2 for d in days}

    def fetch(day: date) -> TargetDecision | None:
        return by_day.get(day)

    bot = r10a_v2_bot(store, fetch=fetch, pool=SyncPool())  # type: ignore[arg-type]
    ex = Ex(store)
    for d in days:
        ex.t = datetime.combine(d.day + timedelta(days=1), datetime.min.time(), UTC) + timedelta(minutes=20)
        close = D(str(d.stored["inputs"]["close_perp"]))
        ex.bid, ex.ask = close, close + D("0.01")
        if d.day == date(2026, 10, 18):
            ex.bid, ex.ask = D("82786.00"), D("82786.01")       # the 10-18 order filled at 82,786.01
        bot._next_try = 0.0
        for _ in range(3):
            bot.step(ex, 1)  # type: ignore[arg-type]
            if store.intents and store.intents[-1][0].client_order_id not in ex.filled:
                ex.fill()
        if d.day == date(2026, 10, 19):
            # 10-20 15:40Z HALT drill: Guardian sold 0.02408 BTC at 82,569.99
            store.held -= D("0.02408")
            store.cash += D("0.02408") * D("82569.99") - D("1.98828536")
    got = {day: r["action"] for day, r in store.rows.items()}
    want = {d.day: d.stored["action"] for d in days if d.day not in WAITS}
    assert got == want
    (buy, price), = store.intents
    assert (buy.side, buy.qty, price, buy.decision_id) == (Side.BUY, D("0.02411"), D("82786.01"), "r10a2-2026-10-18")
    assert store.rows[date(2026, 11, 8)]["outcome"] == store_outcome_11_08(days)
    bot.close()


def store_outcome_11_08(days) -> str:
    (d,) = [x for x in days if x.day == date(2026, 11, 8)]
    return d.stored["outcome"]


def test_target_bot_refuses_perp_strategies():
    class P(R10aV2):
        market = Market.PERP

    with pytest.raises(ValueError, match="Spot books only"):
        TargetBot(Store(), P(), book="x", allocation=D(1), label="x", tag="x")  # type: ignore[arg-type]
