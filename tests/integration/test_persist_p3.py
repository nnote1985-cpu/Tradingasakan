"""Testnet plan P3: durable records on a throwaway local PostgreSQL (docs/testnet-development-plan.md §7, §8.6)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tests.integration.test_migrations import _as, db  # noqa: F401  (module-scoped local PostgreSQL fixture)
from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent
from trading.execution.rules import TradingRules
from trading.execution.safety import AccountSafety
from trading.execution.store import ExecStore, IntentRefused
from trading.execution.types import Side
from trading.ingest.account import Balance, FillEvent
from trading.ingest.bars import Bar
from trading.runtime.persist import PersistStore, ReconcileRecord, Recorder, TradeRecord, split_symbol, trade_lines

psycopg = pytest.importorskip("psycopg")

T0 = datetime(2026, 10, 17, 9, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)
P3_TABLES = ("exec_account_epochs", "market_bars", "market_bar_revisions", "exec_trades", "exec_journal_lines",
             "exec_reconciliations", "exec_balances", "worker_heartbeats")
BAL = {"BTC": Balance(D("1"), D("0")), "USDT": Balance(D("9000"), D("1000")), "XRP": Balance(D("0"), D("0"))}


@pytest.fixture
def store(db):  # noqa: F811
    return PersistStore(db, f"acct-{datetime.now(UTC).timestamp()}")


def bar(minute: int, close: str = "65000", interval: str = "1m") -> Bar:
    t = T0_MS + minute * 60_000
    return Bar("BTCUSDT", interval, t, t + 59_999, D("64900"), D("65100"), D("64800"), D(close), D("12.5"), "mainnet_ws")


def fill(trade_id: int, side: str = "BUY", fee: str = "0.00001", fee_asset: str = "BTC") -> FillEvent:
    return FillEvent("BTCUSDT", 7, "tak-1", trade_id, side, D("0.01"), D("65000"), D(fee), fee_asset, T0_MS)


def check(verdict: str = "MATCH", at_min: int = 0, balances: dict[str, Balance] | None = None, diffs: tuple[str, ...] = ()) -> ReconcileRecord:
    return ReconcileRecord(verdict, T0_MS + at_min * 60_000, at_min == 0, diffs, BAL if balances is None else balances, 0)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_browser_roles_cannot_read_p3_tables(db, role):  # noqa: F811
    for t in P3_TABLES:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(db, role, f"select * from trading.{t}")


def test_service_role_can_read_p3_tables(db):  # noqa: F811
    for t in P3_TABLES:
        _as(db, "service_role", f"select count(*) from trading.{t}")


def test_bars_keep_the_first_value_and_revisions_are_kept_apart(store):
    assert store.add_bars([bar(0), bar(1)]) == 2
    assert store.add_bars([bar(1), bar(2)]) == 1                       # bar 1 again: nothing changes
    assert store.add_bars([bar(0, close="1")]) == 0                     # a different value never overwrites
    assert store.add_revision(bar(0, close="1")) is True
    assert store.add_revision(bar(99)) is False                         # no revision for a bar we never stored
    row = store.conn.execute("select close from trading.market_bars where open_time = %s and interval = '1m'",
                             (T0,)).fetchone()
    assert row[0] == D("65000")
    with pytest.raises(psycopg.errors.RaiseException):
        store.conn.execute("update trading.market_bars set close = 1")
    assert store.last_bar_open("BTCUSDT", "1m") == T0_MS + 2 * 60_000


def test_epoch_opens_once_with_balanced_opening_entry(store):
    e = store.start_epoch(BAL, T0)
    assert e == 1 and store.open_epoch() == 1
    with pytest.raises(RuntimeError, match="already has an open epoch"):
        store.start_epoch(BAL, T0)
    assert store.wallet(1) == {"BTC": D("1"), "USDT": D("10000")}      # zero balances are left out
    store.close_epoch("INTERRUPTED_TESTNET_RESET", "testnet reset confirmed", T0 + timedelta(days=1))
    with pytest.raises(psycopg.errors.RaiseException):                  # closed epochs are never reopened
        store.conn.execute("update trading.exec_account_epochs set status = 'OPEN', closed_at = null where account = %s",
                           (store.account,))
    with pytest.raises(psycopg.errors.RaiseException):
        store.conn.execute("delete from trading.exec_account_epochs where account = %s", (store.account,))
    assert store.start_epoch({"USDT": Balance(D("5000"), D("0"))}, T0 + timedelta(days=1)) == 2
    assert store.wallet(1) == {"BTC": D("1"), "USDT": D("10000")}      # epoch 1 history stays


def test_trade_is_journalled_once_and_balances_per_asset(store):
    e = store.start_epoch(BAL, T0)
    assert store.add_trade(e, TradeRecord(fill(1), T0_MS)) is True
    assert store.add_trade(e, TradeRecord(fill(1), T0_MS + 5)) is False      # the same trade id from a repeated event
    assert store.add_trade(e, TradeRecord(fill(2, side="SELL", fee="0.65", fee_asset="USDT"), T0_MS)) is True
    w = store.wallet(e)
    assert w["BTC"] == D("1") + D("0.01") - D("0.00001") - D("0.01")
    assert w["USDT"] == D("10000") - D("650") + D("650") - D("0.65")
    sums = store.conn.execute("select entry_key, asset, sum(amount) from trading.exec_journal_lines where account = %s "
                              "group by 1, 2 having sum(amount) <> 0", (store.account,)).fetchall()
    assert sums == []


def test_unbalanced_journal_is_refused_at_commit(store):
    e = store.start_epoch(BAL, T0)
    with pytest.raises(psycopg.errors.RaiseException), store.conn.transaction():
        store.conn.execute("insert into trading.exec_journal_lines values (%s, %s, 'x', 'BTC', 'WALLET', 1, 'TRADE', now())",
                           (store.account, e))


def test_trade_lines_split_symbol():
    assert split_symbol("BTCUSDT") == ("BTC", "USDT") and split_symbol("ETHBTC") == ("ETH", "BTC")
    lines = trade_lines(fill(3, fee="0"))
    assert {(a, ledger) for _, a, ledger, _, _ in lines} == {("BTC", "WALLET"), ("BTC", "MARKET"), ("USDT", "WALLET"), ("USDT", "MARKET")}


def test_recorder_opens_epoch_on_first_conclusive_check_and_stores_runs_sparingly(store):
    r = Recorder(store)
    trade = ("trade", TradeRecord(fill(10), T0_MS))
    r.flush([("reconcile", check("INCONCLUSIVE")), trade])
    assert r.epoch is None and r.pending == [trade]                    # trades wait for the epoch
    r.flush([("reconcile", check("MATCH", 1)), ("reconcile", check("MATCH", 2)), ("bar", bar(1000)), ("revision", bar(1000, "1"))])
    assert r.epoch == 1 and r.pending == [] and r.counts == {"bars": 1, "revisions": 1, "trades": 1, "reconciliations": 2}
    r.flush([("reconcile", check("DISCREPANCY", 3, diffs=("order 9 not ours",))), ("reconcile", check("DISCREPANCY", 4)),
             ("reconcile", check("MATCH", 5)), ("reconcile", check("MATCH", 20))])
    verdicts = [v for (v,) in store.conn.execute("select verdict from trading.exec_reconciliations where account = %s order by id",
                                                 (store.account,))]
    assert verdicts == ["INCONCLUSIVE", "MATCH", "DISCREPANCY", "DISCREPANCY", "MATCH", "MATCH"]
    bal = dict(store.conn.execute("select asset, free + locked from trading.exec_balances where account = %s", (store.account,)).fetchall())
    assert bal == {"BTC": D("1"), "USDT": D("10000")}


def test_recorder_keeps_records_when_the_database_fails(store):
    r = Recorder(store)
    r.flush([("reconcile", check())])
    broken = Recorder(PersistStore(store.conn, store.account), epoch=r.epoch)

    def fail(*_a, **_k):
        raise psycopg.OperationalError("down")

    broken.store.add_trade = fail  # type: ignore[method-assign]
    recs = [("trade", TradeRecord(fill(20), T0_MS)), ("trade", TradeRecord(fill(21), T0_MS))]
    with pytest.raises(psycopg.OperationalError):
        broken.flush(recs)
    assert broken.pending == recs
    del broken.store.add_trade
    broken.flush([])
    assert broken.pending == [] and broken.counts["trades"] == 2


def test_recorder_sends_only_bars_newer_than_stored(store):
    r = Recorder(store)
    store.add_bars([bar(5000 + i) for i in range(3)])
    assert r.sync_bars("BTCUSDT", "1m", [bar(5000 + i) for i in range(5)]) == 2


def test_heartbeat_upserts_one_row(store):
    store.heartbeat("pc:1", "pc", T0.isoformat(), T0.isoformat(), {"safety": "RECOVERING"})
    store.heartbeat("pc:2", "pc", T0.isoformat(), (T0 + timedelta(seconds=10)).isoformat(), {"safety": "RECOVERING"})
    rows = store.conn.execute("select owner from trading.worker_heartbeats where account = %s", (store.account,)).fetchall()
    assert rows == [("pc:2",)]


RULES = TradingRules("BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"), min_qty=D("0.00001"), max_qty=D("9000"), min_notional=D("5"))


def test_concurrent_reservations_cannot_spend_the_same_budget(db):  # noqa: F811
    """Two writers accept buys at the same moment; the budget fits one, so exactly one is accepted (§7.3)."""
    account = f"race-{datetime.now(UTC).timestamp()}"
    setup = ExecStore(db, account)
    s = AccountSafety()
    s.recovered(T0, reconciled=True)
    setup.save_safety(s)
    conns = [psycopg.connect(db.info.dsn, autocommit=True) for _ in range(2)]
    barrier, results = threading.Barrier(2), []

    def go(i: int) -> None:
        it = OrderIntent.build(RULES, side=Side.BUY, qty=D("0.05"), mode=TradingMode.TESTNET, book=f"race/{account}",
                               decision_id=f"d{i}", leg="a", created_at=T0, safety_epoch=s.epoch)
        barrier.wait()
        try:
            results.append(ExecStore(conns[i], account).accept_intent(it, unit_price=D("65000"), budget=D("5000")))
        except IntentRefused as e:
            results.append(str(e))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    for c in conns:
        c.close()
    assert sorted(map(str, results)) == ["INSUFFICIENT_BUDGET", "True"]
    assert setup.reserved() == D("3250")


def test_worker_status_sql_reads_what_the_worker_wrote(store):
    from api.workers import SHOWN_ASSETS, WORKERS_SQL, worker_items

    r = Recorder(store)
    r.flush([("reconcile", check("MATCH")), ("trade", TradeRecord(fill(30), T0_MS))])
    store.heartbeat("pc:9", "pc", T0.isoformat(), T0.isoformat(),
                    {"mode": "TESTNET", "safety": "RECOVERING", "clock_offset_ms": 25.0, "clock_status": "OK",
                     "feeds": {"quote_age_ms": 300, "streams": {"klines": {"connected": True}}}})
    rows = [row for row in store.conn.execute(WORKERS_SQL, (list(SHOWN_ASSETS),)).fetchall() if row[0] == store.account]
    [w] = worker_items(rows, now=datetime.now(UTC))
    assert w["silent"] is False and w["mode"] == "TESTNET" and w["clock"]["offset_ms"] == 25.0
    assert w["epoch"]["epoch"] == 1 and w["reconcile"]["verdict"] == "MATCH" and w["trades"] == 1
    assert [b["asset"] for b in w["balances"]] == ["BTC", "USDT"] and w["asset_count"] == 2
    assert w["balances"][1] == {"asset": "USDT", "free": "9000", "locked": "1000"}       # no 18-place trailing zeros
    assert w["streams"] == {"klines": {"connected": True}}
