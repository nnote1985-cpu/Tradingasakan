"""P2 ingestion service: runs the feeds next to the worker (testnet-development-plan.md §3.3, §4, §8.4, §10 P2).

Three streams, each reconnecting on its own:

- signal plane: Mainnet public 1m and 1d klines (combined stream on
  data-stream.binance.vision), with REST backfill from data-api.binance.vision
  on start and after every reconnect, so gaps close from REST, not by guessing;
- execution quotes: Testnet bookTicker for BTCUSDT;
- account: the Testnet WebSocket API user-data stream (session.logon with the
  Ed25519 key), checked against REST account/openOrders on start, after each
  reconnect and every 30 s.

The service runs its own asyncio loop in a thread. The worker reads
``health()`` into the heartbeat and drains ``events`` into alerts every tick,
so feed problems reach Telegram through the same queue as everything else.
What should be stored (closed bars, revisions, Testnet trades, account
checks) goes on ``records``; the worker writes it to Supabase (P3, see
:mod:`trading.runtime.persist`), so this thread never touches the database.
Backfill builds history only; nothing here makes or replays a decision (RT05).
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from trading.ingest.account import AccountState, Verdict
from trading.ingest.bars import INTERVAL_MS, Bar, BarSeries, BarStore, Outcome, bar_from_rest, bar_from_stream
from trading.ingest.quotes import SILENT_AFTER_MS, QuoteBook, quote_from_stream
from trading.ingest.streams import Connect, StreamLoop
from trading.runtime import endpoints
from trading.runtime.alerts import Severity
from trading.runtime.persist import ReconcileRecord, Record, TradeRecord
from trading.runtime.worker import HttpGet, TestnetRest

SYMBOL = "BTCUSDT"
SIGNAL_INTERVALS = ("1m", "1d")
WARMUP_MS = {"1m": 2 * 86_400_000, "1d": 150 * 86_400_000}     # SMA100 needs 100 closed days (§4.3)
RECONCILE_EVERY_S = 30
KLINE_SILENT_AFTER_MS = 180_000
STARTUP_GRACE_MS = 30_000
Event = tuple[Severity, str, str]


def klines_url(symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> str:
    q = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit})
    url = f"https://data-api.binance.vision/api/v3/klines?{q}"
    endpoints.check_public(url, "GET")
    return url


class IngestService:
    def __init__(self, store: BarStore, rest: TestnetRest, *, get: HttpGet = endpoints.get_json, connect: Connect | None = None,
                 now_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        self.store, self.rest, self.get, self.connect, self.now_ms = store, rest, get, connect, now_ms
        self.lock = threading.Lock()
        self.series: dict[str, BarSeries] = {i: store.load(SYMBOL, i, now_ms()) for i in SIGNAL_INTERVALS}
        self.quotes = QuoteBook()
        self.account = AccountState()
        self.events: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self.records: queue.SimpleQueue[Record] = queue.SimpleQueue()
        self.last_reconcile: dict[str, Any] = {}
        self._reconcile_due = True
        self._first_reconcile = True
        self._backfill_due = True
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self.started_ms: int | None = None
        self.loops = self._build_loops()

    # --- streams --------------------------------------------------------------------

    def _build_loops(self) -> list[StreamLoop]:
        names = "/".join(f"{SYMBOL.lower()}@kline_{i}" for i in SIGNAL_INTERVALS)
        loops = [
            StreamLoop("klines", f"wss://data-stream.binance.vision/stream?streams={names}", signed=False,
                       on_message=self._on_kline, on_reconnect=self._after_market_reconnect, connect=self.connect, now_ms=self.now_ms),
            StreamLoop("quotes", f"{endpoints.STREAM_URL['testnet_market']}/{SYMBOL.lower()}@bookTicker", signed=False,
                       on_message=self._on_quote, connect=self.connect, now_ms=self.now_ms),
        ]
        creds = self.rest.creds
        if creds is not None and creds.pem is not None:            # session.logon needs an Ed25519 key
            loops.append(StreamLoop("account", endpoints.STREAM_URL["testnet_ws_api"], signed=True, on_message=self._on_user,
                                    on_open=self._logon, on_reconnect=self._after_account_reconnect, connect=self.connect,
                                    now_ms=self.now_ms))
        return loops

    def _on_kline(self, msg: dict[str, Any], received_ms: int) -> None:
        data = msg.get("data", msg)
        bar = bar_from_stream(data) if isinstance(data, dict) else None
        if bar is not None:
            self._accept(bar, received_ms)

    def _on_quote(self, msg: dict[str, Any], received_ms: int) -> None:
        q = quote_from_stream(msg, received_ms)
        if q is not None:
            self.quotes.accept(q)

    def _on_user(self, msg: dict[str, Any], received_ms: int) -> None:
        event = msg.get("event")
        if not isinstance(event, dict):
            return
        with self.lock:
            n = len(self.account.fills)
            kind = self.account.apply(event, received_ms)
            new = list(self.account.fills.values())[n:]
        for f in new:
            self.records.put(("trade", TradeRecord(f, received_ms)))
        if kind == "eventStreamTerminated":
            self.events.put((Severity.WARNING, "user-stream", "Testnet user stream terminated; reconnecting"))
            self._reconcile_due = True

    async def _logon(self, ws: Any) -> None:
        creds = self.rest.creds
        assert creds is not None
        ts = int(time.time() * 1000 + self.rest.offset_ms)
        payload = f"apiKey={creds.api_key}&timestamp={ts}"
        await ws.send(json.dumps({"id": "logon", "method": "session.logon",
                                  "params": {"apiKey": creds.api_key, "timestamp": ts, "signature": creds.sign(payload)}}))
        await self._expect_ok(ws, "logon")
        await ws.send(json.dumps({"id": "subscribe", "method": "userDataStream.subscribe"}))
        await self._expect_ok(ws, "subscribe")

    @staticmethod
    async def _expect_ok(ws: Any, rid: str) -> None:
        while True:
            resp = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if resp.get("id") == rid:
                if resp.get("status") != 200:
                    err = resp.get("error", {})
                    raise ConnectionError(f"{rid} refused: {err.get('code')} {err.get('msg')}")
                return

    def _after_market_reconnect(self) -> None:
        self._backfill_due = True

    def _after_account_reconnect(self) -> None:
        self._reconcile_due = True

    # --- bars -----------------------------------------------------------------------

    def _accept(self, bar: Bar, now_ms: int) -> Outcome:
        with self.lock:
            series = self.series[bar.interval]
            out = series.accept(bar, now_ms)
            if out in (Outcome.NEW, Outcome.GAP_FILL):
                self.store.append(bar)
                self.records.put(("bar", bar))
        if out is Outcome.REVISION:
            self.records.put(("revision", bar))
            self.events.put((Severity.WARNING, f"revision-{bar.interval}",
                             f"{bar.symbol} {bar.interval} bar at {bar.open_time} came again with different values; first value kept"))
        return out

    def backfill(self) -> int:
        """Fetch every missing closed bar from REST. Returns how many were added."""
        added = 0
        now = self.now_ms()
        for interval in SIGNAL_INTERVALS:
            series = self.series[interval]
            step = INTERVAL_MS[interval]
            with self.lock:
                ranges = series.missing(now, since=now // step * step - WARMUP_MS[interval])
            for a, b in ranges:
                t = a
                while t <= b:
                    status, rows = self.get(klines_url(SYMBOL, interval, t, b + step - 1), {})
                    if status != 200 or not isinstance(rows, list) or not rows:
                        if status != 200:
                            self.events.put((Severity.WARNING, "backfill", f"kline backfill {interval} failed (HTTP {status})"))
                        break
                    for row in rows:
                        if self._accept(bar_from_rest(SYMBOL, interval, row), now) in (Outcome.NEW, Outcome.GAP_FILL):
                            added += 1
                    t = int(rows[-1][0]) + step
        return added

    # --- account check ----------------------------------------------------------------

    def reconcile(self) -> Verdict | None:
        if self.rest.creds is None:
            return None
        requested = self.now_ms()
        s1, acct = self.rest.account()
        s2, orders = self.rest.open_orders(SYMBOL)
        if s1 != 200 or s2 != 200 or not isinstance(acct, dict) or not isinstance(orders, list):
            self.events.put((Severity.WARNING, "reconcile", f"account check failed (HTTP {s1}/{s2}); entries stay paused"))
            self.reconcile_failed(requested)
            return None
        with self.lock:
            first = self._first_reconcile
            r = self.account.reconcile(acct, orders, requested, first=first)
            balances, n_orders = dict(self.account.balances), len(self.account.open_orders)
        self.records.put(("reconcile", ReconcileRecord(r.verdict.value, requested, first, r.differences, balances, n_orders)))
        if r.verdict is not Verdict.INCONCLUSIVE:
            self._first_reconcile = False
        self.last_reconcile = {"verdict": r.verdict.value, "at_ms": requested, "differences": list(r.differences)}
        if r.verdict is Verdict.DISCREPANCY:
            self.events.put((Severity.CRITICAL, "reconcile", "account reconcile discrepancy: " + "; ".join(r.differences)[:600]))
        return r.verdict

    def reconcile_failed(self, at_ms: int) -> None:
        """A failed read replaces the last verdict, so an old MATCH never outlives the check that should have renewed it."""
        self.last_reconcile = {"verdict": "UNAVAILABLE", "at_ms": at_ms, "differences": []}

    # --- lifecycle ------------------------------------------------------------------

    async def _housekeeping(self, stop: asyncio.Event) -> None:
        next_reconcile = next_backfill = 0.0
        while not stop.is_set():
            if self._backfill_due and time.monotonic() >= next_backfill:
                self._backfill_due = False
                try:
                    await asyncio.to_thread(self.backfill)
                except Exception as e:  # noqa: BLE001 - network errors must not stop the feeds; retry later
                    self._backfill_due = True
                    next_backfill = time.monotonic() + RECONCILE_EVERY_S
                    self.events.put((Severity.WARNING, "backfill", f"kline backfill failed: {type(e).__name__}"))
            if self._reconcile_due or time.monotonic() >= next_reconcile:
                try:
                    verdict = await asyncio.to_thread(self.reconcile)
                except Exception as e:  # noqa: BLE001
                    verdict = None
                    self.reconcile_failed(self.now_ms())
                    self.events.put((Severity.WARNING, "reconcile", f"account check failed: {type(e).__name__}; entries stay paused"))
                self._reconcile_due = verdict in (None, Verdict.INCONCLUSIVE) and self.rest.creds is not None
                next_reconcile = time.monotonic() + RECONCILE_EVERY_S
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        await asyncio.gather(self._housekeeping(self._stop), *(lp.run(self._stop) for lp in self.loops))

    def start(self) -> None:
        self.started_ms = self.now_ms()
        self._thread = threading.Thread(target=asyncio.run, args=(self._main(),), name="ingest", daemon=True)
        self._thread.start()

    def closed_bars(self, interval: str) -> list[Bar]:
        with self.lock:
            return self.series[interval].closed()

    def stop(self, timeout: float = 5) -> None:
        if self._loop is not None and self._stop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout)

    def health(self) -> dict[str, Any]:
        now = self.now_ms()
        with self.lock:
            s1m, s1d = self.series["1m"], self.series["1d"]
            h: dict[str, Any] = {
                "bars_1m_last_open": s1m.last_open, "bars_1d_last_open": s1d.last_open,
                "bar_gaps": len(s1m.gaps) + len(s1d.gaps), "bar_revisions": s1m.revisions + s1d.revisions,
                "quote_age_ms": self.quotes.age_ms(SYMBOL, now),
                "quote_bid": None if (q := self.quotes.latest.get(SYMBOL)) is None else str(q.bid),
                "quote_ask": None if q is None else str(q.ask),
                "balances": {a: str(b.free + b.locked) for a, b in self.account.balances.items() if a in ("BTC", "USDT")},
                "open_orders": len(self.account.open_orders), "fills": len(self.account.fills),
                "reconcile": self.last_reconcile.get("verdict"),
            }
        h["alive"] = self._thread is not None and self._thread.is_alive()
        h["streams"] = {lp.name: {"connected": lp.connected, "connects": lp.connects,
                                  "silent_ms": None if lp.last_message_ms is None else now - lp.last_message_ms,
                                  "error": lp.last_error} for lp in self.loops}
        return h

    def problems(self) -> list[Event]:
        """Health checks the worker turns into alerts each tick (the alert queue's cooldown stops repeats)."""
        h = self.health()
        out: list[Event] = []
        if self.started_ms is None or self.now_ms() - self.started_ms < STARTUP_GRACE_MS:
            return out
        if not h["alive"]:
            return [(Severity.CRITICAL, "feeds", "feed thread stopped; restart the worker")]
        age = h["quote_age_ms"]
        if age is None or age > SILENT_AFTER_MS:
            seen = "none yet" if age is None else f"{age / 1000:.0f} s"
            out.append((Severity.WARNING, "quotes", f"Testnet quotes silent ({seen}); entries blocked"))
        k = h["streams"]["klines"]["silent_ms"]
        if k is None or k > KLINE_SILENT_AFTER_MS:
            out.append((Severity.WARNING, "klines", "Mainnet kline stream silent; bars will backfill after reconnect"))
        acct = h["streams"].get("account")
        if acct is not None and not acct["connected"]:
            out.append((Severity.WARNING, "user-stream", f"Testnet user stream down ({acct['error'] or 'connecting'})"))
        return out
