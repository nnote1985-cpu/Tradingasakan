"""The supervised P1 worker (testnet-development-plan.md §2, §10 P1).

It runs on its own, apart from the web app and API, so closing the browser or
restarting the API does not touch it (RT01). Every tick (2 s) it renews the
writer lease, writes a heartbeat file, and on its own schedule checks the
clock against the Testnet server, pings the external dead-man and resends
queued alerts. It sends no orders: P1 only proves the process, mode, key and
alert plumbing that P2–P4 build on.

Startup always reports safety as RECOVERING (or the latched HALT state from
the store). With TESTNET execution enabled the worker refuses to run
without the database lease, because the lease is what keeps one writer per
account (§2, RT07). Losing the lease stops the worker; the process manager
restarts it, and the new run waits for the lease like any other owner.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from trading.config import Settings
from trading.contracts.enums import TradingMode
from trading.execution.safety import AccountSafety
from trading.runtime import endpoints
from trading.runtime.alerts import AlertQueue, Severity
from trading.runtime.clock import ClockReading, ClockStatus, reading
from trading.runtime.credentials import TestnetCredentials, mask
from trading.runtime.deadman import PING_EVERY_S, DeadMan

WORKER_MODES = frozenset({TradingMode.SHADOW, TradingMode.TESTNET})
HttpGet = Callable[[str, dict[str, str]], tuple[int, Any]]


class WorkerRefused(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class LeaseStore(Protocol):
    def acquire_lease(self, owner: str, now: datetime, ttl: timedelta) -> Any: ...
    def load_safety(self) -> AccountSafety: ...


def http_get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingasakan-worker", **headers})
    try:
        with endpoints.opener().open(req, timeout=10) as r:
            status, body = int(r.status), r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, None


class TestnetRest:
    """Read-only Testnet REST for P1: server time and the account check. Every URL passes the endpoint guards."""

    __test__ = False

    def __init__(self, creds: TestnetCredentials | None, get: HttpGet = http_get, wall_ms: Callable[[], float] | None = None) -> None:
        self.creds, self._get = creds, get
        self._wall_ms = wall_ms or (lambda: time.time() * 1000)
        self.offset_ms = 0.0

    def clock(self) -> ClockReading:
        url = endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, "/api/v3/time")
        endpoints.check_public(url, "GET")
        before = self._wall_ms()
        status, data = self._get(url, {})
        after = self._wall_ms()
        if status != 200 or not isinstance(data, dict) or "serverTime" not in data:
            raise ConnectionError(f"Testnet time check failed (HTTP {status})")
        r = reading(before, float(data["serverTime"]), after)
        self.offset_ms = r.offset_ms
        return r

    def account(self) -> tuple[int, Any]:
        if self.creds is None:
            raise WorkerRefused("no Testnet credentials loaded")
        q = urllib.parse.urlencode({"omitZeroBalances": "true", "recvWindow": 5000,
                                    "timestamp": int(self._wall_ms() + self.offset_ms)})
        q += "&signature=" + urllib.parse.quote(self.creds.sign(q), safe="")
        url = endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, "/api/v3/account", q)
        endpoints.check_signed(url)
        return self._get(url, {"X-MBX-APIKEY": self.creds.api_key})


@dataclass
class WorkerConfig:
    owner: str
    heartbeat_path: Path
    tick_s: float = 2.0
    lease_ttl: timedelta = timedelta(seconds=15)
    clock_every_s: float = 60.0
    deadman_every_s: float = PING_EVERY_S
    flush_every_s: float = 30.0


@dataclass
class WorkerState:
    safety: str = "RECOVERING"
    lease_token: int | None = None
    clock_offset_ms: float | None = None
    clock_status: str | None = None
    signing_paused: bool = True          # until the first clock check passes
    account_ok: bool | None = None
    deadman_ok: bool | None = None
    started_at: str = ""
    last_tick_at: str = ""
    _due: dict[str, float] = field(default_factory=dict)


class Worker:
    def __init__(self, settings: Settings, cfg: WorkerConfig, rest: TestnetRest, alerts: AlertQueue, *,
                 deadman: DeadMan | None = None, store: LeaseStore | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), monotonic: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print) -> None:
        if settings.trading_mode not in WORKER_MODES:
            raise WorkerRefused(f"the worker runs in SHADOW or TESTNET, not {settings.trading_mode.value}")
        if settings.testnet_execution_enabled:
            if store is None:
                raise WorkerRefused("TESTNET execution needs DATABASE_URL: the writer lease keeps one writer per account")
            if rest.creds is None:
                raise WorkerRefused("TESTNET execution needs Testnet credentials")
        self.settings, self.cfg, self.rest, self.alerts = settings, cfg, rest, alerts
        self.deadman, self.store, self.now, self.mono, self.log = deadman, store, now, monotonic, log
        self.state = WorkerState()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        s = self.state
        s.started_at = self.now().isoformat(timespec="seconds")
        safety = self.store.load_safety() if self.store is not None else AccountSafety()
        s.safety = safety.state.value
        self._renew_lease()
        mode = self.settings.trading_mode.value + (" + execution" if self.settings.testnet_execution_enabled else "")
        self.alerts.push(Severity.WARNING, "start", f"worker started: {mode}, safety {s.safety}, "
                         f"lease {'token ' + str(s.lease_token) if s.lease_token else 'none (no database)'}")
        self._check_clock()
        if self.rest.creds is not None:
            self._check_account()
        self._ping()
        self.alerts.flush()
        t = self.mono()
        s._due |= {"clock": t + self.cfg.clock_every_s, "deadman": t + self.cfg.deadman_every_s, "flush": t + self.cfg.flush_every_s}
        self._heartbeat()

    def tick(self) -> None:
        self._renew_lease()
        if self._due("clock", self.cfg.clock_every_s):
            self._check_clock()
        if self.deadman is not None and self._due("deadman", self.cfg.deadman_every_s):
            self._ping()
        if self._due("flush", self.cfg.flush_every_s):
            self.alerts.flush()
        self._heartbeat()

    def stop(self, reason: str) -> None:
        self.alerts.push(Severity.INFO, "stop", f"worker stopped: {reason}")

    def run(self, should_stop: Callable[[], bool], sleep: Callable[[float], None] = time.sleep) -> None:
        self.start()
        while not should_stop():
            sleep(self.cfg.tick_s)
            self.tick()

    # --- steps --------------------------------------------------------------

    def _due(self, name: str, every: float) -> bool:
        t = self.mono()
        if t >= self.state._due.get(name, t):
            self.state._due[name] = t + every
            return True
        return False

    def _renew_lease(self) -> None:
        if self.store is None:
            return
        try:
            lease = self.store.acquire_lease(self.cfg.owner, self.now(), self.cfg.lease_ttl)
        except Exception as e:
            self.alerts.push(Severity.CRITICAL, "lease", f"database unreachable, stopping: {type(e).__name__}")
            raise LeaseLost("database unreachable") from e
        if lease is None:
            self.alerts.push(Severity.CRITICAL, "lease", "another worker holds the writer lease for this account; stopping")
            raise LeaseLost("writer lease held by another owner")
        if self.state.lease_token is not None and lease.token != self.state.lease_token:
            raise LeaseLost(f"lease token changed {self.state.lease_token} -> {lease.token}")
        self.state.lease_token = lease.token

    def _check_clock(self) -> None:
        s = self.state
        try:
            r = self.rest.clock()
        except (ConnectionError, OSError, endpoints.EndpointRefused) as e:
            s.signing_paused = True
            self.alerts.push(Severity.WARNING, "testnet", f"Testnet unreachable, signed calls paused: {e}")
            return
        prev = s.clock_status
        s.clock_offset_ms, s.clock_status = round(r.offset_ms, 1), r.status.value
        s.signing_paused = r.status is ClockStatus.PAUSE
        if r.status is ClockStatus.PAUSE:
            self.alerts.push(Severity.CRITICAL, "clock", f"clock offset {r.offset_ms:+.0f} ms to Testnet; signed calls paused. Sync the Host clock")
        elif r.status is ClockStatus.WARN:
            self.alerts.push(Severity.WARNING, "clock",
                             f"clock offset {r.offset_ms:+.0f} ms to Testnet (warn at 500, pause at 1000). Sync the Host clock")
        elif prev in (ClockStatus.WARN.value, ClockStatus.PAUSE.value):
            self.alerts.push(Severity.INFO, "clock-ok", f"clock back in range ({r.offset_ms:+.0f} ms)")

    def _check_account(self) -> None:
        try:
            status, data = self.rest.account()
        except Exception as e:  # noqa: BLE001 - any failure here is a credential incident, reported without the key
            status, data = 0, {"msg": type(e).__name__}
        ok = status == 200 and isinstance(data, dict) and data.get("canTrade") is True
        self.state.account_ok = ok
        key = mask(self.rest.creds.api_key) if self.rest.creds else ""
        if ok:
            self.log(f"Testnet account ok (key {key}, {self.rest.creds.key_type if self.rest.creds else ''})")
        else:
            msg = data.get("msg") if isinstance(data, dict) else None
            self.alerts.push(Severity.CRITICAL, "credential", f"Testnet key {key} failed the account check (HTTP {status}"
                             f"{', ' + str(msg) if msg else ''}); no orders will be sent")

    def _ping(self) -> None:
        if self.deadman is None:
            return
        ok = self.deadman.ping()
        if self.state.deadman_ok is not False and not ok:
            self.alerts.push(Severity.WARNING, "deadman", "dead-man ping failed; the external monitor may alert soon")
        self.state.deadman_ok = ok

    def _heartbeat(self) -> None:
        s = self.state
        s.last_tick_at = self.now().isoformat(timespec="seconds")
        body = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        body |= {"pid": os.getpid(), "owner": self.cfg.owner, "mode": self.settings.trading_mode.value,
                 "execution_enabled": self.settings.testnet_execution_enabled}
        path = self.cfg.heartbeat_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
        tmp.replace(path)
