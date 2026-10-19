"""Reconnecting WebSocket loops (testnet-development-plan.md §4, §8.4; RT03, RT37).

``StreamLoop`` connects, runs an optional ``on_open`` handshake, hands each
JSON message to ``on_message`` and, when the connection drops, waits with a
capped exponential backoff before reconnecting. Every reconnect calls
``on_reconnect`` so the owner can backfill bars or reconcile the account
before trusting the stream again. Every URL passes the endpoint guards.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from trading.runtime import endpoints

Connect = Callable[..., Any]          # websockets.asyncio.client.connect, or a fake in tests


def _default_connect() -> Connect:
    from websockets.asyncio.client import connect

    return connect


@dataclass
class Backoff:
    first: float = 1.0
    cap: float = 60.0
    _next: float = 0.0

    def next(self) -> float:
        self._next = self.first if self._next == 0 else min(self._next * 2, self.cap)
        return self._next

    def reset(self) -> None:
        self._next = 0.0


class StreamLoop:
    def __init__(self, name: str, url: str, *, signed: bool, on_message: Callable[[dict[str, Any], int], None],
                 on_open: Callable[[Any], Awaitable[None]] | None = None, on_reconnect: Callable[[], None] | None = None,
                 connect: Connect | None = None, backoff: Backoff | None = None,
                 now_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        endpoints.check_stream(url, signed=signed)
        self.name, self.url, self.on_message, self.on_open, self.on_reconnect = name, url, on_message, on_open, on_reconnect
        self.connect = connect or _default_connect()
        self.backoff = backoff or Backoff()
        self.now_ms = now_ms
        self.connected = False
        self.connects = 0
        self.last_message_ms: int | None = None
        self.last_error: str | None = None

    async def run(self, stop: asyncio.Event, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        while not stop.is_set():
            try:
                async with self.connect(self.url, open_timeout=10, close_timeout=2, ping_interval=20) as ws:
                    self.connected = True
                    self.connects += 1
                    if self.connects > 1 and self.on_reconnect is not None:
                        self.on_reconnect()
                    if self.on_open is not None:
                        await self.on_open(ws)
                    async for raw in ws:
                        msg = json.loads(raw)
                        self.last_message_ms = self.now_ms()
                        self.backoff.reset()
                        if isinstance(msg, dict):
                            self.on_message(msg, self.last_message_ms)
                        if stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure means reconnect
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                self.connected = False
            if not stop.is_set():
                await sleep(self.backoff.next())
