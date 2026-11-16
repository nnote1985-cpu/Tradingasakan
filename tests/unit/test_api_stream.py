"""SSE change notices (m1-realtime-plan.md §3.1, §5): watcher logic with a fake cursor source and the endpoint's auth."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.main import create_app
from api.stream import ChangeWatcher, event_stream, snapshot
from trading.config import Settings
from trading.contracts.enums import OperatorRole

NOW = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
VIEWER, STRANGER = UUID(int=301), UUID(int=302)


def row(account="DEFAULT", updated=NOW, bid="95000.1", orders="0/", trades="0/", decisions="0//", risk="0", requests="0/0/",
        reconcile="7"):
    return (account, updated, bid, "95000.2", "850", "NORMAL", "TESTNET", orders, trades, decisions, risk, requests, reconcile)


class Source:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.fail = False

    async def cursor_rows(self):
        self.calls += 1
        if self.fail:
            raise OSError("db down")
        return list(self.rows)


def test_snapshot_marks_silent_workers_and_keeps_price_text():
    cursors, beats = snapshot([row(), row("AI", updated=NOW - timedelta(seconds=61), bid=None)], NOW)
    assert cursors["DEFAULT"]["heartbeat"] == NOW.isoformat() and cursors["DEFAULT"]["reconcile"] == "7"
    assert beats[0] == {"account": "DEFAULT", "updated_at": NOW.isoformat(), "silent": False, "quote_bid": "95000.1",
                        "quote_ask": "95000.2", "quote_age_ms": 850, "safety": "NORMAL", "mode": "TESTNET"}
    assert beats[1]["silent"] is True and beats[1]["quote_bid"] is None


async def _drain(q, n, timeout=2.0):
    return [await asyncio.wait_for(q.get(), timeout) for _ in range(n)]


def test_hello_then_only_what_changed():
    async def go():
        src = Source([row()])
        w = ChangeWatcher(src, poll_every=0.01, clock=lambda: NOW)
        q = w.subscribe()
        [(ev, hello)] = await _drain(q, 1)
        assert ev == "hello" and hello["cursors"]["DEFAULT"]["trades"] == "0/" and hello["heartbeats"][0]["quote_bid"] == "95000.1"
        src.rows = [row(trades="1/2026-11-16 11:00:01+00", requests="1/4/")]
        [(ev, data)] = await _drain(q, 1)
        assert ev == "changed" and data == {"account": "DEFAULT", "kinds": ["trades", "requests"]}
        src.rows = [row(updated=NOW + timedelta(seconds=10), bid="95010", trades="1/2026-11-16 11:00:01+00", requests="1/4/")]
        [(ev, data)] = await _drain(q, 1)
        assert ev == "heartbeat" and data["heartbeats"][0]["quote_bid"] == "95010"
        await asyncio.sleep(0.05)
        assert q.empty()                     # nothing new: no events
        w.unsubscribe(q)
        await asyncio.sleep(0.05)
        assert not w.running                 # nobody listens: the poller stops
        calls = src.calls
        await asyncio.sleep(0.05)
        assert src.calls == calls
    asyncio.run(go())


def test_one_poller_for_many_streams_and_a_late_joiner_gets_hello():
    async def go():
        src = Source([row()])
        w = ChangeWatcher(src, poll_every=0.02, clock=lambda: NOW)
        a, b = w.subscribe(), w.subscribe()
        await _drain(a, 1)
        await _drain(b, 1)
        await asyncio.sleep(0.2)
        assert src.calls <= 15               # ~10 reads in 0.2 s for two streams, not ~20
        c = w.subscribe()
        [(ev, _)] = await _drain(c, 1)
        assert ev == "hello"
        for q in (a, b, c):
            w.unsubscribe(q)
    asyncio.run(go())


def test_a_slow_reader_is_resynced_with_hello_instead_of_growing_memory():
    async def go():
        src = Source([row()])
        w = ChangeWatcher(src, poll_every=0.01, clock=lambda: NOW)
        q = w.subscribe()
        await _drain(q, 1)
        for i in range(64):
            q.put_nowait(("changed", {"n": i}))
        src.rows = [row(trades="1/")]          # the next change overflows the queue
        await asyncio.sleep(0.05)
        events = [q.get_nowait()[0] for _ in range(q.qsize())]
        assert events == ["hello"]              # backlog dropped, one full resync instead
        w.unsubscribe(q)
    asyncio.run(go())


def test_database_errors_keep_the_stream_and_show_in_ping():
    async def go():
        src = Source([row()])
        w = ChangeWatcher(src, poll_every=0.01, clock=lambda: NOW)
        q = w.subscribe()
        await _drain(q, 1)
        src.fail = True
        await asyncio.sleep(0.05)
        assert w.running and w.ping()[1]["db_ok"] is False
        src.fail = False
        await asyncio.sleep(0.05)
        assert w.ping()[1]["db_ok"] is True
        w.unsubscribe(q)
    asyncio.run(go())


def test_stream_pings_when_quiet_and_says_bye_at_max_age():
    async def go():
        w = ChangeWatcher(Source([row()]), poll_every=0.01, clock=lambda: NOW)

        async def connected():
            return False
        frames = [f async for f in event_stream(w, connected, max_age=0.25, ping_every=0.05)]
        assert frames[0] == "retry: 5000\n\n"
        names = [f.split("\n")[1].removeprefix("event: ") for f in frames[1:]]
        assert names[0] == "hello" and "ping" in names and names[-1] == "bye"
        ids = [int(f.split("\n")[0].removeprefix("id: ")) for f in frames[1:]]
        assert ids == list(range(1, len(ids) + 1))
        await asyncio.sleep(0.05)
        assert not w.running and not w.queues
    asyncio.run(go())


class Verifier:
    def verify(self, token):
        try:
            return UUID(token)
        except ValueError as e:
            raise AuthError("invalid_token", "invalid") from e


class Roles:
    async def active_roles(self, user):
        return frozenset({OperatorRole.VIEWER}) if user == VIEWER else frozenset()


def _app(src):
    return create_app(Settings(), Verifier(), Roles(), cursors=src, stream_max_age=0.5, stream_ping_every=0.1)


def test_endpoint_needs_a_viewer_token_in_the_header():
    with TestClient(_app(Source([row()]))) as c:
        assert c.get("/api/v1/stream").status_code == 401
        assert c.get("/api/v1/stream", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/api/v1/stream", headers={"Authorization": f"Bearer {STRANGER}"}).status_code == 403
        assert c.get(f"/api/v1/stream?access_token={VIEWER}").status_code == 401     # no token in the URL


def test_endpoint_streams_hello_heartbeat_and_bye():
    src = Source([row()])
    with TestClient(_app(src)) as c, c.stream("GET", "/api/v1/stream", headers={"Authorization": f"Bearer {VIEWER}"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        text = "".join(r.iter_text())
    events = [(b.split("\nevent: ")[1].split("\n")[0], json.loads(b.split("\ndata: ")[1]))
              for b in text.split("\n\n") if "\nevent: " in b]
    assert events[0][0] == "hello" and events[0][1]["heartbeats"][0]["account"] == "DEFAULT"
    assert events[-1] == ("bye", {"reason": "max_age"})


@pytest.mark.parametrize("missing", [None])
def test_without_a_database_the_stream_still_answers(missing):
    app = create_app(Settings(), Verifier(), Roles(), stream_max_age=0.2, stream_ping_every=0.05)
    with TestClient(app) as c, c.stream("GET", "/api/v1/stream", headers={"Authorization": f"Bearer {VIEWER}"}) as r:
        text = "".join(r.iter_text())
    assert "event: hello" in text and '"cursors":{}' in text
