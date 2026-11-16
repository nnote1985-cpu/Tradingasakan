"""Change notices for the web over Server-Sent Events (m1-realtime-plan.md §3.1, GET /api/v1/stream).

One ``ChangeWatcher`` per API process reads a small cursor per Testnet account
once a second, only while at least one stream is open, and fans the result out
to every stream. A stream says *what* changed; the web refetches the REST
endpoint it already uses, so a dropped or resumed stream never loses or
duplicates a row. Nothing here writes to the database.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool

from api.workers import SILENT_AFTER

log = logging.getLogger(__name__)

POLL_EVERY = 1.0           # seconds between cursor reads while someone listens
PING_EVERY = 15.0          # seconds of quiet before a ping
MAX_AGE = 600.0            # a stream closes itself after 10 minutes; the web reconnects with a fresh token
QUEUE_SIZE = 64
KINDS = ("orders", "trades", "decisions", "risk", "requests", "reconcile")

CURSOR_SQL = """
select h.account, h.updated_at,
       h.body -> 'feeds' ->> 'quote_bid', h.body -> 'feeds' ->> 'quote_ask', h.body -> 'feeds' ->> 'quote_age_ms',
       h.body ->> 'safety', h.body ->> 'mode',
       (select count(*) || '/' || coalesce(max(x.at)::text, '') from trading.exec_order_events x
          join trading.exec_intents i on i.client_order_id = x.client_order_id where i.account = h.account),
       (select count(*) || '/' || coalesce(max(t.received_at)::text, '') from trading.exec_trades t where t.account = h.account),
       (select count(*) || '/' || coalesce(max(d.decided_at)::text, '') || '/' || coalesce(max(d.executed_at)::text, '')
          from trading.exec_strategy_decisions d where d.account = h.account),
       (select coalesce(max(r.id), 0)::text from trading.exec_risk_events r where r.account = h.account),
       (select count(*) || '/' || coalesce(max(q.id), 0) || '/' || coalesce(max(q.handled_at)::text, '')
          from trading.exec_operator_requests q where q.account = h.account),
       (select coalesce(max(c.id), 0)::text from trading.exec_reconciliations c where c.account = h.account)
from trading.worker_heartbeats h
order by h.account
"""


class CursorSource(Protocol):
    async def cursor_rows(self) -> list[tuple[Any, ...]]: ...


class NoCursors:
    async def cursor_rows(self) -> list[tuple[Any, ...]]:
        return []


class PgCursorSource:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def cursor_rows(self) -> list[tuple[Any, ...]]:
        try:
            async with self.pool.connection() as conn:
                return list(await (await conn.execute(CURSOR_SQL)).fetchall())
        except pg_errors.UndefinedTable:
            return []        # before the Testnet migrations there is nothing to watch


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def snapshot(rows: list[tuple[Any, ...]], now: datetime) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Cursor per account and kind, plus the heartbeat summary the status bar shows."""
    cursors: dict[str, dict[str, str]] = {}
    beats: list[dict[str, Any]] = []
    for account, updated, bid, ask, age, safety, mode, *marks in rows:
        cursors[account] = {"heartbeat": _iso(updated) or "", **dict(zip(KINDS, (m or "" for m in marks), strict=True))}
        beats.append({"account": account, "updated_at": _iso(updated),
                      "silent": updated is None or now - updated > SILENT_AFTER,
                      "quote_bid": bid, "quote_ask": ask, "quote_age_ms": None if age is None else int(age),
                      "safety": safety, "mode": mode})
    return cursors, beats


Event = tuple[str, dict[str, Any]]


class ChangeWatcher:
    """Single poller per process; runs only while a stream is open."""

    def __init__(self, source: CursorSource, poll_every: float = POLL_EVERY,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.source = source
        self.poll_every = poll_every
        self.clock = clock
        self.queues: set[asyncio.Queue[Event]] = set()
        self.fresh: set[asyncio.Queue[Event]] = set()      # get a full hello on the next read
        self.cursors: dict[str, dict[str, str]] | None = None
        self.polls = 0
        self.db_ok = True
        self.last_poll_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.queues.add(q)
        self.fresh.add(q)
        if self._task is None or self._task.done():
            self.cursors = None
            self._task = asyncio.get_running_loop().create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self.queues.discard(q)
        self.fresh.discard(q)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _put(self, q: asyncio.Queue[Event], ev: Event) -> None:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:          # a slow reader: drop its backlog and resync it with a hello
            while not q.empty():
                q.get_nowait()
            self.fresh.add(q)

    async def _run(self) -> None:
        while self.queues:
            try:
                rows = await self.source.cursor_rows()
                self.db_ok = True
            except Exception:  # noqa: BLE001 — a database blip must not end the stream; pings report it
                log.warning("stream cursor read failed", exc_info=True)
                self.db_ok = False
                await asyncio.sleep(self.poll_every)
                continue
            self.polls += 1
            now = self.clock()
            self.last_poll_at = now
            cursors, beats = snapshot(rows, now)
            prev = self.cursors
            self.cursors = cursors
            if self.fresh:
                hello = ("hello", {"server_time": now.isoformat(), "cursors": cursors, "heartbeats": beats})
                for q in list(self.fresh):
                    self.fresh.discard(q)
                    self._put(q, hello)
            if prev is not None:
                if any(prev.get(a, {}).get("heartbeat") != c["heartbeat"] for a, c in cursors.items()) or prev.keys() != cursors.keys():
                    self._broadcast(("heartbeat", {"server_time": now.isoformat(), "heartbeats": beats}))
                for account, c in cursors.items():
                    kinds = [k for k in KINDS if prev.get(account, {}).get(k) != c[k]]
                    if kinds:
                        self._broadcast(("changed", {"account": account, "kinds": kinds}))
            await asyncio.sleep(self.poll_every)

    def _broadcast(self, ev: Event) -> None:
        for q in list(self.queues):
            if q not in self.fresh:          # a queue waiting for hello gets the full picture instead
                self._put(q, ev)

    def ping(self) -> Event:
        return ("ping", {"server_time": self.clock().isoformat(), "db_ok": self.db_ok,
                         "last_poll_at": _iso(self.last_poll_at)})


def frame(seq: int, event: str, data: dict[str, Any]) -> str:
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def event_stream(watcher: ChangeWatcher, is_disconnected: Callable[[], Any],
                       max_age: float = MAX_AGE, ping_every: float = PING_EVERY) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    q = watcher.subscribe()
    deadline = loop.time() + max_age
    seq = 0
    try:
        yield "retry: 5000\n\n"
        while (left := deadline - loop.time()) > 0:
            if await is_disconnected():
                break
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=min(ping_every, left))
            except TimeoutError:
                if deadline - loop.time() <= 0:
                    break
                event, data = watcher.ping()
            seq += 1
            yield frame(seq, event, data)
        seq += 1
        yield frame(seq, "bye", {"reason": "max_age"})
    finally:
        watcher.unsubscribe(q)
