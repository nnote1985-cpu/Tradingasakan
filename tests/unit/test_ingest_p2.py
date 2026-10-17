"""P2 ingestion: closed bars, quotes, user stream and REST reconciliation fixtures (testnet-development-plan.md §10 P2)."""

from __future__ import annotations

import asyncio
import json
import queue
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from trading.config import Settings
from trading.ingest.account import AccountState, Verdict
from trading.ingest.bars import Bar, BarSeries, BarStore, Outcome, aggregate, bar_from_stream
from trading.ingest.quotes import Quote, QuoteBook, quote_from_stream
from trading.ingest.service import IngestService
from trading.ingest.streams import Backoff, StreamLoop
from trading.runtime import endpoints
from trading.runtime.alerts import AlertQueue, Severity
from trading.runtime.credentials import TestnetCredentials
from trading.runtime.worker import TestnetRest, Worker, WorkerConfig

M = 60_000
T0 = 1_792_195_200_000          # 2026-10-17 00:00 UTC, a whole day in ms
NOW = T0 + 10 * M + 5


def bar(i: int, close: str = "100", interval: str = "1m", source: str = "mainnet_ws") -> Bar:
    step = {"1m": M, "1d": 1440 * M}[interval]
    t = T0 + i * step
    c = D(close)
    return Bar("BTCUSDT", interval, t, t + step - 1, c, c + 1, c - 1, c, D("2"), source)


def kline_row(i: int, close: str = "100") -> list:
    b = bar(i, close)
    return [b.open_time, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume), b.close_time, "0", 1, "0", "0", "0"]


# --- bars (RT02, RT04, RT05) -------------------------------------------------------------------------------

def test_unclosed_candles_never_enter():
    msg = {"e": "kline", "k": {"s": "BTCUSDT", "i": "1m", "t": T0, "T": T0 + M - 1, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "x": False}}
    assert bar_from_stream(msg) is None
    msg["k"]["x"] = True
    assert bar_from_stream(msg) == Bar("BTCUSDT", "1m", T0, T0 + M - 1, D(1), D(1), D(1), D(1), D(1), "mainnet_ws")
    s = BarSeries("BTCUSDT", "1m")
    assert s.accept(bar(9), now_ms=T0 + 9 * M + 30_000) is Outcome.UNCLOSED
    assert s.last_open is None


def test_duplicate_and_revision_keep_the_first_value():
    s = BarSeries("BTCUSDT", "1m")
    assert s.accept(bar(1), NOW) is Outcome.NEW
    assert s.accept(bar(1), NOW) is Outcome.DUPLICATE
    assert s.accept(bar(1, close="101"), NOW) is Outcome.REVISION
    assert s.bars[bar(1).open_time].close == D(100) and (s.duplicates, s.revisions) == (1, 1)


def test_gaps_are_recorded_and_filled_out_of_order():
    s = BarSeries("BTCUSDT", "1m")
    s.accept(bar(1), NOW)
    assert s.accept(bar(5), NOW) is Outcome.NEW
    assert s.gaps == [(bar(2).open_time, bar(4).open_time)]
    assert s.accept(bar(3), NOW) is Outcome.GAP_FILL
    assert s.gaps == [(bar(2).open_time, bar(2).open_time), (bar(4).open_time, bar(4).open_time)]
    assert s.accept(bar(0), NOW) is Outcome.STALE
    s.accept(bar(2), NOW)
    s.accept(bar(4), NOW)
    assert s.gaps == [] and [b.open_time for b in s.closed()] == [bar(i).open_time for i in range(1, 6)]


def test_misaligned_bar_is_refused():
    s = BarSeries("BTCUSDT", "1m")
    b = bar(1)
    assert s.accept(Bar(b.symbol, b.interval, b.open_time + 1, b.close_time + 1, *b.values(), b.source), NOW) is Outcome.MISALIGNED


def test_missing_covers_gaps_and_the_tail_up_to_the_last_closed_bar():
    s = BarSeries("BTCUSDT", "1m")
    s.accept(bar(1), NOW)
    s.accept(bar(3), NOW)
    assert s.missing(NOW) == [(bar(2).open_time, bar(2).open_time), (bar(4).open_time, bar(9).open_time)]
    assert BarSeries("BTCUSDT", "1m").missing(NOW, since=bar(8).open_time) == [(bar(8).open_time, bar(9).open_time)]


def test_aggregate_only_complete_buckets():
    one_hour = [bar(i) for i in range(60)]
    out = aggregate(one_hour + [bar(61)], "1h")
    assert len(out) == 1 and out[0].open_time == T0 and out[0].close_time == T0 + 60 * M - 1
    assert out[0].volume == D(120) and out[0].source == "agg_1m"
    assert aggregate(one_hour[:-1], "1h") == []


def test_store_restores_series_and_gaps_after_restart(tmp_path):
    store = BarStore(tmp_path)
    s = BarSeries("BTCUSDT", "1m")
    for i in (1, 2, 5):
        s.accept(bar(i), NOW)
        store.append(bar(i))
    again = store.load("BTCUSDT", "1m", NOW)
    assert again.last_open == bar(5).open_time and again.gaps == [(bar(3).open_time, bar(4).open_time)]
    assert again.closed() == s.closed()


# --- quotes ---------------------------------------------------------------------------------------------------

def test_quotes_ignore_out_of_order_and_crossed_books():
    book = QuoteBook()
    q = quote_from_stream({"u": 10, "s": "BTCUSDT", "b": "100", "B": "1", "a": "101", "A": "1"}, received_ms=1000)
    assert q is not None and book.accept(q)
    assert not book.accept(Quote("BTCUSDT", D(99), D(1), D(100), D(1), 9, 2000))
    assert not book.accept(Quote("BTCUSDT", D(102), D(1), D(101), D(1), 11, 2000))
    assert book.out_of_order == 1 and book.latest["BTCUSDT"].update_id == 10
    assert book.fresh_for_entry("BTCUSDT", 6000) is not None and book.fresh_for_entry("BTCUSDT", 6001) is None


# --- account stream and reconciliation (RT02, RT03, RT39) ------------------------------------------------------

def exec_report(x: str, status: str, trade_id: int = -1, oid: int = 7, client: str = "tak-abc", z: str = "0") -> dict:
    return {"e": "executionReport", "E": 1, "s": "BTCUSDT", "c": client, "S": "BUY", "x": x, "X": status, "i": oid,
            "l": "0.001", "z": z, "L": "100", "n": "0.000001", "N": "BTC", "T": 5, "t": trade_id}


def rest_account(usdt: str = "10000", btc: str = "1") -> dict:
    return {"balances": [{"asset": "USDT", "free": usdt, "locked": "0"}, {"asset": "BTC", "free": btc, "locked": "0"}]}


def test_repeated_fill_is_counted_once_and_terminal_order_closes():
    a = AccountState()
    a.apply(exec_report("NEW", "NEW"), 10)
    assert 7 in a.open_orders
    a.apply(exec_report("TRADE", "FILLED", trade_id=42, z="0.001"), 11)
    a.apply(exec_report("TRADE", "FILLED", trade_id=42, z="0.001"), 12)
    assert len(a.fills) == 1 and a.duplicate_fills == 1 and 7 not in a.open_orders


def test_reconcile_adopts_first_snapshot_then_matches():
    a = AccountState()
    assert a.reconcile(rest_account(), [], requested_ms=100, first=True).verdict is Verdict.MATCH
    a.apply({"e": "outboundAccountPosition", "E": 1, "B": [{"a": "USDT", "f": "9900", "l": "0"}]}, 150)
    assert a.reconcile(rest_account(usdt="9900"), [], requested_ms=200).verdict is Verdict.MATCH


def test_reconcile_reports_balance_difference_and_adopts_rest():
    a = AccountState()
    a.reconcile(rest_account(), [], 100, first=True)
    r = a.reconcile(rest_account(btc="0.5"), [], 200)
    assert r.verdict is Verdict.DISCREPANCY and "BTC: stream 1/0, REST 0.5/0" in r.differences[0]
    assert a.balances["BTC"].free == D("0.5")


def test_reconcile_is_inconclusive_when_stream_moved_during_the_read():
    a = AccountState()
    a.reconcile(rest_account(), [], 100, first=True)
    a.apply({"e": "outboundAccountPosition", "E": 1, "B": [{"a": "BTC", "f": "0.9", "l": "0"}]}, 300)
    assert a.reconcile(rest_account(), [], requested_ms=250).verdict is Verdict.INCONCLUSIVE
    assert a.balances["BTC"].free == D("0.9")


def test_manual_and_missed_orders_are_discrepancies():
    a = AccountState()
    a.reconcile(rest_account(), [], 100, first=True)
    a.apply(exec_report("NEW", "NEW", oid=7), 110)
    manual = {"symbol": "BTCUSDT", "orderId": 9, "clientOrderId": "web_abc", "status": "NEW", "executedQty": "0"}
    r = a.reconcile(rest_account(), [manual], 200)
    assert r.verdict is Verdict.DISCREPANCY
    assert any("order 7 open in stream state" in d for d in r.differences)
    assert any("order 9 (web_abc) was not placed by the bot" in d for d in r.differences)


# --- stream loop (RT03, RT37) ------------------------------------------------------------------------------

class FakeWS:
    def __init__(self, messages, fail_after=True, replies=None):
        self.messages, self.fail_after, self.sent = list(messages), fail_after, []
        self.replies = list(replies or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return json.dumps(self.replies.pop(0))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        if self.fail_after:
            raise ConnectionError("dropped")
        raise StopAsyncIteration


def test_stream_reconnects_with_backoff_and_signals_reconnect():
    sessions = [FakeWS([{"n": 1}]), FakeWS([{"n": 2}])]
    opened: list[str] = []
    got, reconnects, sleeps = [], [], []
    stop = asyncio.Event()

    def connect(url, **kw):
        opened.append(url)
        if not sessions:
            stop.set()
            raise ConnectionError("no more")
        return sessions.pop(0)

    async def sleep(s):
        sleeps.append(s)

    loop = StreamLoop("t", "wss://data-stream.binance.vision/ws/x", signed=False, on_message=lambda m, t: got.append(m["n"]),
                      on_reconnect=lambda: reconnects.append(1), connect=connect, now_ms=lambda: 0)
    asyncio.run(loop.run(stop, sleep=sleep))
    assert got == [1, 2] and len(reconnects) == 1 and loop.connects == 2
    assert sleeps[:2] == [1.0, 1.0]                      # a message resets the backoff
    assert "dropped" in (loop.last_error or "") or "no more" in (loop.last_error or "")


def test_backoff_is_capped():
    b = Backoff(first=1, cap=8)
    assert [b.next() for _ in range(6)] == [1, 2, 4, 8, 8, 8]


def test_streams_only_reach_allowed_hosts():
    with pytest.raises(endpoints.EndpointRefused):
        StreamLoop("x", "wss://stream.binance.com/ws/btcusdt@trade", signed=False, on_message=lambda m, t: None, connect=lambda u, **k: None)
    with pytest.raises(endpoints.EndpointRefused):
        StreamLoop("x", "wss://ws-api.binance.com/ws-api/v3", signed=True, on_message=lambda m, t: None, connect=lambda u, **k: None)
    with pytest.raises(endpoints.EndpointRefused):
        StreamLoop("x", "ws://data-stream.binance.vision/ws", signed=False, on_message=lambda m, t: None, connect=lambda u, **k: None)


# --- service: backfill, reconcile, logon --------------------------------------------------------------------

def _pem_creds(tmp_path) -> TestnetCredentials:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption())
    return TestnetCredentials("qehUKEY", pem=pem)


class KlineServer:
    """Serves GET /api/v3/klines for the 1m series from a fixed set of rows, 3 per page; 1d returns nothing."""

    def __init__(self, rows):
        self.rows, self.urls = rows, []

    def __call__(self, url, headers):
        self.urls.append(url)
        assert url.startswith("https://data-api.binance.vision/api/v3/klines?") and not headers
        from urllib.parse import parse_qs, urlsplit

        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if q["interval"] != "1m":
            return 200, []
        start, end = int(q["startTime"]), int(q["endTime"])
        return 200, [r for r in self.rows if start <= r[0] <= end][:3]


def _service(tmp_path, get=None, rest=None, now=NOW):
    return IngestService(BarStore(tmp_path / "bars"), rest or TestnetRest(None), get=get or KlineServer([]),
                         connect=lambda u, **k: None, now_ms=lambda: now)


def test_backfill_fills_the_tail_and_a_stream_gap_from_rest(tmp_path):
    server = KlineServer([kline_row(i) for i in range(10)])
    svc = _service(tmp_path, get=server)
    svc._accept(bar(2), NOW)
    svc._accept(bar(6), NOW)                                    # stream jumped: gap 3..5
    added = svc.backfill()
    s = svc.series["1m"]
    assert s.gaps == [] and s.last_open == bar(9).open_time and added == 6
    assert [b.open_time for b in s.closed()] == [bar(i).open_time for i in range(2, 10)]
    again = BarStore(tmp_path / "bars").load("BTCUSDT", "1m", NOW)            # restart keeps it
    assert again.closed() == s.closed()


def test_revision_from_backfill_raises_a_warning_event(tmp_path):
    svc = _service(tmp_path, get=KlineServer([kline_row(2, close="105")]))
    svc._accept(bar(2), NOW)
    svc._accept(bar(4), NOW)
    svc.series["1m"].gaps.append((bar(2).open_time, bar(2).open_time))       # make backfill ask REST for bar 2 again
    svc.backfill()
    events = []
    while True:
        try:
            events.append(svc.events.get_nowait())
        except queue.Empty:
            break
    assert any(sev is Severity.WARNING and key == "revision-1m" for sev, key, _ in events)


class FakeRest(TestnetRest):
    def __init__(self, creds, account, orders):
        super().__init__(creds, get=lambda u, h: (200, {}), wall_ms=lambda: 0.0)
        self._acct, self._orders = account, orders

    def account(self):
        return self._acct

    def open_orders(self, symbol):
        return self._orders


def test_reconcile_discrepancy_is_a_critical_event(tmp_path):
    rest = FakeRest(TestnetCredentials("k", secret="s"), (200, rest_account()), (200, []))
    svc = _service(tmp_path, rest=rest)
    assert svc.reconcile() is Verdict.MATCH                  # first snapshot is adopted
    rest._acct = (200, rest_account(btc="0.7"))
    assert svc.reconcile() is Verdict.DISCREPANCY
    sev, key, text = svc.events.get_nowait()
    assert sev is Severity.CRITICAL and key == "reconcile" and "BTC" in text


def test_reconcile_failure_is_a_warning_not_a_guess(tmp_path):
    rest = FakeRest(TestnetCredentials("k", secret="s"), (401, {"msg": "bad"}), (200, []))
    svc = _service(tmp_path, rest=rest)
    assert svc.reconcile() is None
    assert svc.events.get_nowait()[0] is Severity.WARNING and svc.account.balances == {}


def test_user_stream_logs_on_with_a_signed_request(tmp_path):
    creds = _pem_creds(tmp_path)
    svc = _service(tmp_path, rest=TestnetRest(creds, get=lambda u, h: (200, {}), wall_ms=lambda: 0.0))
    assert [lp.name for lp in svc.loops] == ["klines", "quotes", "account"]
    ws = FakeWS([], replies=[{"id": "logon", "status": 200}, {"id": "subscribe", "status": 200}])
    asyncio.run(svc._logon(ws))
    logon, sub = ws.sent
    assert logon["method"] == "session.logon" and logon["params"]["apiKey"] == "qehUKEY" and logon["params"]["signature"]
    assert sub["method"] == "userDataStream.subscribe"
    bad = FakeWS([], replies=[{"id": "logon", "status": 401, "error": {"code": -1022, "msg": "Signature invalid"}}])
    with pytest.raises(ConnectionError, match="-1022"):
        asyncio.run(svc._logon(bad))


def test_hmac_key_runs_without_user_stream(tmp_path):
    svc = _service(tmp_path, rest=TestnetRest(TestnetCredentials("k", secret="s")))
    assert [lp.name for lp in svc.loops] == ["klines", "quotes"]


def test_user_events_update_state_and_termination_forces_reconcile(tmp_path):
    svc = _service(tmp_path)
    svc._reconcile_due = False
    svc._on_user({"subscriptionId": 0, "event": exec_report("NEW", "NEW")}, 10)
    assert 7 in svc.account.open_orders
    svc._on_user({"subscriptionId": 0, "event": {"e": "eventStreamTerminated", "E": 2}}, 11)
    assert svc._reconcile_due and svc.events.get_nowait()[1] == "user-stream"


def test_problems_wait_for_startup_grace_then_report_silence(tmp_path):
    now = [NOW]
    svc = IngestService(BarStore(tmp_path / "b"), TestnetRest(None), get=KlineServer([]), connect=lambda u, **k: None,
                        now_ms=lambda: now[0])
    alive = [True]

    class Thread:
        def is_alive(self):
            return alive[0]

    svc.started_ms, svc._thread = NOW, Thread()
    assert svc.problems() == []
    now[0] = NOW + 31_000
    keys = {k for _, k, _ in svc.problems()}
    assert keys == {"quotes", "klines"}
    svc.quotes.accept(Quote("BTCUSDT", D(1), D(1), D(2), D(1), 1, now[0]))
    assert {k for _, k, _ in svc.problems()} == {"klines"}
    alive[0] = False
    assert [(sev, k) for sev, k, _ in svc.problems()] == [(Severity.CRITICAL, "feeds")]


# --- worker carries the feeds ---------------------------------------------------------------------------------

class FakeFeeds:
    def __init__(self):
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=5):
        self.stopped = True

    def health(self):
        return {"quote_age_ms": 120}

    def problems(self):
        return [(Severity.WARNING, "klines", "Mainnet kline stream silent")]


def test_worker_starts_feeds_drains_events_and_writes_their_health(tmp_path):
    sent: list[str] = []

    class Sink:
        def send(self, text):
            sent.append(text)

    feeds = FakeFeeds()
    q = AlertQueue(tmp_path / "a.jsonl", Sink(), "pc", lambda: datetime(2026, 10, 17, tzinfo=UTC), log=lambda m: None)
    rest = TestnetRest(None, get=lambda u, h: (200, {"serverTime": 0}), wall_ms=lambda: 0.0)
    w = Worker(Settings(), WorkerConfig("o", tmp_path / "hb.json"), rest, q, feeds=feeds, log=lambda m: None)
    w.start()
    feeds.events.put((Severity.CRITICAL, "reconcile", "account reconcile discrepancy: BTC"))
    w.tick()
    hb = json.loads(Path(tmp_path / "hb.json").read_text())
    assert feeds.started and hb["feeds"] == {"quote_age_ms": 120}
    assert any("reconcile discrepancy" in s for s in sent) and any("kline stream silent" in s for s in sent)
    w.stop("test")
    assert feeds.stopped
