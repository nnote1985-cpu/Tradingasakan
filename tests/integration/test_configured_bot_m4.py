"""M4: the worker side of configured bots (m4-bot-config-plan.md §3.5; G1, G2, G3, G7, G8) on a throwaway
local PostgreSQL and an in-memory Binance."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from scripts import testnet_worker as tw
from tests.fake_binance import FakeBinance
from tests.integration.test_executor_p4 import Alerts, Rig
from tests.integration.test_migrations import db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from tests.integration.test_r10a_p5 import SyncPool
from trading.bots import config, loader
from trading.bots.loader import BotLoadRefused
from trading.contracts.strategy import Market, TargetDecision
from trading.execution.binance_spot import BinanceSpotTestnet
from trading.execution.safety import SafetyState
from trading.execution.store import ExecStore
from trading.runtime.configured_bot import CHECK_EVERY_S, ConfiguredBot
from trading.runtime.executor import Executor
from trading.runtime.guardian import GuardedBook, Guardian
from trading.runtime.persist import PersistStore
from trading.runtime.worker import WorkerRefused

psycopg = pytest.importorskip("psycopg")
OPERATOR = uuid.UUID(int=301)


def body(account, **over):
    return {"name": "R10a v2 test", "account": account, "venue": "SPOT_TESTNET", "instrument": {"symbol": "BTCUSDT", "market": "SPOT"},
            "strategy": {"template": "tn-r10a-code-v2", "version": "tn-r10a-code-v2", "timeframe": "1d", "params": {}},
            "allocation": {"asset": "USDT", "amount": "2000"}, "mode": "SHADOW", **over}


class Bots:
    """Writes bots the way the API does (rows + events), with an operator who may approve."""

    def __init__(self, conn) -> None:
        self.conn = conn
        conn.execute("insert into trading.operator_roles (user_id, role) values (%s, 'operator') on conflict do nothing", (OPERATOR,))

    def save(self, bot, rev, account, stored_hash=None, **over):
        b, h = config.normalize(config.parse(body(account, **over)))
        self.conn.execute("insert into trading.bots (bot_id, revision, account, body, hash, saved_by, request_id) "
                          "values (%s, %s, %s, %s, %s, %s, %s)",
                          (bot, rev, account, psycopg.types.json.Jsonb(b), stored_hash or h, OPERATOR, uuid.uuid4()))
        return stored_hash or h

    def event(self, bot, rev, h, *actions):
        for a in actions:
            self.conn.execute("insert into trading.bot_approvals (bot_id, revision, hash, action, actor) values (%s, %s, %s, %s, %s)",
                              (bot, rev, h, a, OPERATOR))

    def approved(self, account, **over):
        bot = uuid.uuid4()
        h = self.save(bot, 1, account, **over)
        self.event(bot, 1, h, "submit", "approve")
        return bot


def acct() -> str:
    return f"m4-{uuid.uuid4().hex[:12]}"


# --- loading (G1, G2, G3) -------------------------------------------------------------------

def test_only_an_approved_revision_is_loaded(db):  # noqa: F811
    bots, a = Bots(db), acct()
    bot = uuid.uuid4()
    h1 = bots.save(bot, 1, a)
    with pytest.raises(BotLoadRefused) as e:
        loader.load_for_account(db, a)
    assert e.value.code == "no_approved_bot"                     # a draft is never used
    bots.event(bot, 1, h1, "submit")
    with pytest.raises(BotLoadRefused):
        loader.load_for_account(db, a)                           # nor a submitted one
    bots.event(bot, 1, h1, "approve")
    h2 = bots.save(bot, 2, a, mode="TESTNET")
    bots.event(bot, 2, h2, "submit")
    got = loader.load_for_account(db, a)
    assert (got.revision, got.config.mode, got.hash) == (1, "SHADOW", h1)   # rev 2 is newer but not approved


def test_hash_mismatch_refuses(db):  # noqa: F811
    bots, a = Bots(db), acct()
    bot = uuid.uuid4()
    forged = "f" * 64                                            # stored hash that the body does not produce
    bots.save(bot, 1, a, stored_hash=forged)
    bots.event(bot, 1, forged, "submit", "approve")
    with pytest.raises(BotLoadRefused) as e:
        loader.load_for_account(db, a)
    assert e.value.code == "hash_mismatch"


def test_two_approved_bots_on_one_account_refuse(db):  # noqa: F811
    bots, a = Bots(db), acct()
    bots.approved(a)
    db.execute("alter table trading.bot_approvals disable trigger bot_approvals_check")   # what the DB normally refuses
    try:
        bots.approved(a)
    finally:
        db.execute("alter table trading.bot_approvals enable trigger bot_approvals_check")
    with pytest.raises(BotLoadRefused) as e:
        loader.load_for_account(db, a)
    assert e.value.code == "two_bots"


def test_worker_refuses_and_alerts_once_per_reason(db, tmp_path):  # noqa: F811
    store = ExecStore(db, acct())
    alerts = Alerts()
    alerts.flush = lambda: 0                                     # type: ignore[method-assign]
    for _ in range(2):                                           # the process manager restarts it: one alert, not two
        with pytest.raises(WorkerRefused, match="no_approved_bot"):
            tw.configured_bot(store, store.account, alerts, tmp_path)
    assert [k for _s, k, _t in alerts.items] == ["bot-config"]
    assert json.loads((tmp_path / "bot-refused.json").read_text())["code"] == "no_approved_bot"


# --- running (SHADOW, heartbeat, switch, retire) --------------------------------------------

class BotRig(Rig):
    def __init__(self, db, mode="SHADOW") -> None:  # noqa: F811
        Rig._day[0] += 30
        self.t = datetime(2027, 6, 1, 0, 0, 5, tzinfo=UTC) + timedelta(days=Rig._day[0])
        self.mono_s = 1000.0
        self.account = acct()
        self.store = ExecStore(db, self.account)
        self.persist = PersistStore(db, self.account)
        self.ex = FakeBinance()
        self.adapter = BinanceSpotTestnet(self.ex)
        self.alerts = Alerts()
        self.verdict, self.checked_at = "MATCH", None
        self.token = self.store.acquire_lease("test", self.t, timedelta(days=30)).token
        self.bots = Bots(db)
        self.bot_id = self.bots.approved(self.account, mode=mode)
        self.weights: dict[date, D | None] = {}
        self.executor = self.make(False)

    def fetch(self, day: date) -> TargetDecision | None:
        if day not in self.weights:
            return None
        w = self.weights[day]
        return TargetDecision.make(strategy_version="tn-r10a-code-v2", instrument="BTCUSDT", market=Market.SPOT, day=day,
                                   target_weight=w, reason=f"test weight {w}", inputs={"w": None if w is None else str(w)},
                                   data_ready_at=self.t)

    def make(self, conformance: bool = False) -> Executor:
        ex = super().make(conformance=False)
        rev = loader.load_for_account(self.store.conn, self.account)
        self.bot = ConfiguredBot.build(self.store, rev, fetch=self.fetch, pool=SyncPool())
        ex.r10a = self.bot  # type: ignore[assignment]
        ex.guardian = Guardian(self.store, [GuardedBook(self.bot.book, self.bot.allocation)])
        return ex

    @property
    def day(self) -> date:
        return self.t.date() - timedelta(days=1)

    def decision(self) -> dict:
        row = self.store.load_decision(self.bot.book, self.day)
        assert row is not None
        return row

    def orders(self) -> int:
        return self.store.conn.execute("select count(*) from trading.exec_intents where account = %s", (self.account,)).fetchone()[0]

    def held(self) -> D:
        return self.store.position(self.bot.book, "BTCUSDT", "BTC", "USDT")[0]


def started(r: BotRig) -> BotRig:
    r.executor.start(r.token)
    r.tick()
    assert r.executor.safety.state is SafetyState.RUNNING
    r.advance(600)
    return r


def test_shadow_records_decisions_and_sends_nothing(db):  # noqa: F811
    r = started(BotRig(db))
    r.weights[r.day] = D("1")
    r.tick(4)
    d = r.decision()
    assert d["action"] == "OPEN" and d["outcome"] == "shadow: no order sent (SHADOW mode)" and r.orders() == 0
    r.advance(86400)
    r.weights[r.day] = D("1")
    r.tick(4)
    assert r.decision()["action"] == "HOLD"                      # flat in SHADOW is not "off target"
    beat = r.executor.body()["bot"]
    assert beat == {"bot_id": str(r.bot_id), "revision": 1, "hash": r.bot.rev.hash, "mode": "SHADOW", "name": "R10a v2 test",
                    "book": r.bot.book, "pending_revision": None, "retired": False, "note": None}
    assert r.executor.body()["r10a"]["book"] == r.bot.book


def test_testnet_trades_and_a_new_revision_waits_for_a_flat_book(db):  # noqa: F811
    r = started(BotRig(db, mode="TESTNET"))
    r.weights[r.day] = D("1")
    r.tick(6)
    assert r.held() > 0 and r.orders() == 1
    h2 = r.bots.save(r.bot_id, 2, r.account, mode="SHADOW", name="renamed")
    r.bots.event(r.bot_id, 2, h2, "submit", "approve")
    r.advance(CHECK_EVERY_S)
    r.tick()
    beat = r.executor.body()["bot"]
    assert beat["revision"] == 1 and beat["pending_revision"] == 2 and "waiting" in beat["note"]
    r.advance(86400)
    r.weights[r.day] = D("0")                                    # the strategy sells; then the book is flat
    r.tick(8)
    beat = r.executor.body()["bot"]
    assert r.held() == 0 and beat["revision"] == 2 and beat["mode"] == "SHADOW" and beat["pending_revision"] is None
    assert any(k.endswith("-switch") for _s, k, _t in r.alerts.items)


def test_a_new_revision_with_other_money_is_not_used(db):  # noqa: F811
    r = started(BotRig(db))
    db.execute("alter table trading.bot_approvals disable trigger bot_approvals_check")
    try:                                                         # the API refuses this; the worker must too
        h2 = r.bots.save(r.bot_id, 2, r.account, allocation={"asset": "USDT", "amount": "1500"})
        r.bots.event(r.bot_id, 2, h2, "submit", "approve")
    finally:
        db.execute("alter table trading.bot_approvals enable trigger bot_approvals_check")
    r.advance(CHECK_EVERY_S)
    r.tick()
    assert r.executor.body()["bot"]["revision"] == 1 and r.executor.body()["bot"]["pending_revision"] is None
    assert any(s.value == "CRITICAL" and k.endswith("-config") for s, k, _t in r.alerts.items)


def test_retired_bot_stops_deciding(db):  # noqa: F811
    r = started(BotRig(db))
    r.bots.event(r.bot_id, 1, r.bot.rev.hash, "retire")
    r.weights[r.day] = D("1")
    r.advance(CHECK_EVERY_S)
    r.tick(4)
    assert r.store.load_decision(r.bot.book, r.day) is None and r.executor.body()["bot"]["retired"] is True


def test_env_bots_add_no_bot_key(db):  # noqa: F811
    r = Rig(db, conformance=False)
    assert "bot" not in r.executor.body()
