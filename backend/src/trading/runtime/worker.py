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

Since P3 the worker also writes durable records (bars, trades, account
checks, heartbeat) through :class:`trading.runtime.persist.Recorder` every
10 s on the lease connection.

Since P4 an :class:`~trading.runtime.executor.Executor` (when given) runs
every tick after the lease renewal: it reconciles orders left by an earlier
run, decides when RECOVERING may become RUNNING, and sends and follows
Testnet orders. It is only ready while the clock check allows signing and
the Testnet key passed its account check.
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
from trading.runtime.executor import LeaseFenced
from trading.runtime.persist import Recorder

WORKER_MODES = frozenset({TradingMode.SHADOW, TradingMode.TESTNET})
HttpGet = Callable[[str, dict[str, str]], tuple[int, Any]]
HttpSend = Callable[[str, str, dict[str, str]], tuple[int, Any, dict[str, str]]]
http_get = endpoints.get_json


class WorkerRefused(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class Feeds(Protocol):
    """The P2 ingestion service, as the worker sees it."""
    events: Any
    records: Any

    def closed_bars(self, interval: str) -> list[Any]: ...
    def start(self) -> None: ...
    def stop(self, timeout: float = 5) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def problems(self) -> list[tuple[Severity, str, str]]: ...


class OrderRunner(Protocol):
    """The P4 executor, as the worker sees it."""
    safety: AccountSafety
    started: bool

    def start(self, token: int) -> None: ...
    def tick(self, token: int, *, ready: bool) -> None: ...
    def drain_records(self) -> list[Any]: ...
    def body(self) -> dict[str, Any]: ...


class LeaseStore(Protocol):
    def acquire_lease(self, owner: str, now: datetime, ttl: timedelta) -> Any: ...
    def load_safety(self) -> AccountSafety: ...


class TestnetRest:
    """Testnet REST: server time, the account check and (since P4) signed order calls. Every URL passes the endpoint guards.

    ``call`` sends one request and never retries it. After a 429 or 418 it refuses further
    calls until the Retry-After time has passed, so a ban is not extended by more traffic.
    """

    __test__ = False

    def __init__(self, creds: TestnetCredentials | None, get: HttpGet = http_get, wall_ms: Callable[[], float] | None = None,
                 send: HttpSend = endpoints.request_json) -> None:
        self.creds, self._get, self._send = creds, get, send
        self._wall_ms = wall_ms or (lambda: time.time() * 1000)
        self.offset_ms = 0.0
        self.blocked_until_ms = 0.0

    @property
    def rate_limited(self) -> bool:
        return self._wall_ms() < self.blocked_until_ms

    def call(self, method: str, path: str, params: dict[str, Any], *, signed: bool = True) -> tuple[int, Any]:
        """One Testnet request. Raises :class:`RateLimited` without sending while a Retry-After is running."""
        if self.rate_limited:
            raise endpoints.RateLimited(f"Binance asked us to wait {int((self.blocked_until_ms - self._wall_ms()) / 1000)} s more")
        if signed:
            if self.creds is None:
                raise WorkerRefused("no Testnet credentials loaded")
            q = urllib.parse.urlencode(params | {"recvWindow": 5000, "timestamp": int(self._wall_ms() + self.offset_ms)})
            q += "&signature=" + urllib.parse.quote(self.creds.sign(q), safe="")
            url = endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, path, q)
            endpoints.check_signed(url)
            headers = {"X-MBX-APIKEY": self.creds.api_key}
        else:
            url = endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, path, urllib.parse.urlencode(params))
            endpoints.check_public(url, method)
            headers = {}
        status, data, hdrs = self._send(method, url, headers)
        if status in (418, 429):
            wait = next((v for k, v in hdrs.items() if k.lower() == "retry-after"), "60")
            self.blocked_until_ms = self._wall_ms() + 1000 * (float(wait) if str(wait).replace(".", "", 1).isdigit() else 60.0)
            raise endpoints.RateLimited(f"Binance answered HTTP {status}; waiting {wait} s")
        return status, data

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

    def _signed(self, path: str, params: dict[str, Any]) -> tuple[int, Any]:
        if self.creds is None:
            raise WorkerRefused("no Testnet credentials loaded")
        q = urllib.parse.urlencode(params | {"recvWindow": 5000, "timestamp": int(self._wall_ms() + self.offset_ms)})
        q += "&signature=" + urllib.parse.quote(self.creds.sign(q), safe="")
        url = endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, path, q)
        endpoints.check_signed(url)
        return self._get(url, {"X-MBX-APIKEY": self.creds.api_key})

    def account(self) -> tuple[int, Any]:
        return self._signed("/api/v3/account", {"omitZeroBalances": "true"})

    def open_orders(self, symbol: str) -> tuple[int, Any]:
        return self._signed("/api/v3/openOrders", {"symbol": symbol})


@dataclass
class WorkerConfig:
    owner: str
    heartbeat_path: Path
    tick_s: float = 2.0
    lease_ttl: timedelta = timedelta(seconds=15)
    clock_every_s: float = 60.0
    deadman_every_s: float = PING_EVERY_S
    flush_every_s: float = 30.0
    persist_every_s: float = 10.0
    host: str = ""


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
                 deadman: DeadMan | None = None, store: LeaseStore | None = None, feeds: Feeds | None = None,
                 recorder: Recorder | None = None, executor: OrderRunner | None = None,
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
        self.feeds, self.recorder, self.executor = feeds, recorder, executor
        self.persist_ok: bool | None = None
        self.state = WorkerState()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        s = self.state
        s.started_at = self.now().isoformat(timespec="seconds")
        safety = self.store.load_safety() if self.store is not None else AccountSafety()
        s.safety = safety.state.value
        self._renew_lease()
        mode = self.settings.trading_mode.value + (" + execution" if self.settings.testnet_execution_enabled else "")
        self.alerts.push(Severity.WARNING, "start", "worker เริ่มทำงาน ปกติเกิดหลัง update หรือ restart ถ้าไม่ได้สั่งเองและเด้งบ่อย "
                         "แปลว่า worker ล้มแล้วเริ่มใหม่ ให้ดูหน้า Worker "
                         f"({mode}, สถานะ {s.safety}, สิทธิ์เขียน {'#' + str(s.lease_token) if s.lease_token else 'ไม่มี (ไม่มีฐานข้อมูล)'})")
        self._check_clock()
        if self.rest.creds is not None:
            self._check_account()
        self._ping()
        if self.recorder is not None and self.feeds is not None:
            self._sync_bars()
        if self.feeds is not None:
            self.feeds.start()
        self._start_executor()
        self.alerts.flush()
        t = self.mono()
        s._due |= {"clock": t + self.cfg.clock_every_s, "account": t + self.cfg.clock_every_s, "deadman": t + self.cfg.deadman_every_s,
                   "flush": t + self.cfg.flush_every_s, "persist": t + self.cfg.persist_every_s}
        self._heartbeat()
        self._persist()

    def tick(self) -> None:
        self._renew_lease()
        if self._due("clock", self.cfg.clock_every_s):
            self._check_clock()
        if self.deadman is not None and self._due("deadman", self.cfg.deadman_every_s):
            self._ping()
        # A failed account check at start (often the network, not the key) kept orders off for two days on 2026-10-26:
        # until it passes, check again every minute.
        if self.rest.creds is not None and self.state.account_ok is not True and self._due("account", self.cfg.clock_every_s):
            self._check_account()
        if self._due("flush", self.cfg.flush_every_s):
            self.alerts.flush()
        self._drain_feeds()
        self._run_executor()
        self._heartbeat()
        if self._due("persist", self.cfg.persist_every_s):
            self._persist()

    def stop(self, reason: str) -> None:
        if self.feeds is not None:
            self.feeds.stop()
        self.alerts.push(Severity.INFO, "stop", f"worker หยุดแล้ว ({reason})")

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
            self.alerts.push(Severity.CRITICAL, "lease", "ต่อฐานข้อมูล Supabase ไม่ได้ worker จึงหยุดตัวเองเพื่อความปลอดภัย Windows จะเปิดให้ใหม่เอง "
                             f"ถ้าเด้งซ้ำหลายรอบให้เช็กเน็ตหรือหน้า Supabase ({type(e).__name__})")
            raise LeaseLost("database unreachable") from e
        if lease is None:
            self.alerts.push(Severity.CRITICAL, "lease", "มี worker อีกตัวถือสิทธิ์เขียนบัญชีนี้อยู่ ตัวนี้จึงหยุดเอง "
                             "กันไม่ให้ส่งคำสั่งซ้ำ ถ้าไม่ได้เปิด worker สองเครื่องก็ไม่ต้องทำอะไร")
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
            self.alerts.push(Severity.WARNING, "testnet", f"ต่อ Binance Testnet ไม่ได้ หยุดส่งคำสั่งไว้ก่อน จะลองใหม่เอง ถ้าหายเองในไม่กี่นาทีไม่ต้องทำอะไร ({e})")
            return
        prev = s.clock_status
        s.clock_offset_ms, s.clock_status = round(r.offset_ms, 1), r.status.value
        s.signing_paused = r.status is ClockStatus.PAUSE
        if r.status is ClockStatus.PAUSE:
            self.alerts.push(Severity.CRITICAL, "clock", f"นาฬิกา PC คลาดจาก Testnet {r.offset_ms:+.0f} ms (เกิน 1 วินาที) หยุดส่งคำสั่งไว้แล้ว "
                             "ให้ sync เวลา Windows: Settings > Time & language > Sync now")
        elif r.status is ClockStatus.WARN:
            self.alerts.push(Severity.WARNING, "clock",
                             f"นาฬิกา PC คลาดจาก Testnet {r.offset_ms:+.0f} ms ยังส่งคำสั่งได้ (เตือนที่ 500 หยุดที่ 1000) "
                             "ถ้าเตือนบ่อยให้ sync เวลา Windows")
        elif prev in (ClockStatus.WARN.value, ClockStatus.PAUSE.value):
            self.alerts.push(Severity.INFO, "clock-ok", f"นาฬิกา PC กลับมาปกติ ({r.offset_ms:+.0f} ms)")

    def _check_account(self) -> None:
        try:
            status, data = self.rest.account()
        except Exception as e:  # noqa: BLE001 - any failure here is a credential incident, reported without the key
            status, data = 0, {"msg": type(e).__name__}
        ok = status == 200 and isinstance(data, dict) and data.get("canTrade") is True
        was = self.state.account_ok
        self.state.account_ok = ok
        key = mask(self.rest.creds.api_key) if self.rest.creds else ""
        if ok:
            self.log(f"Testnet account ok (key {key}, {self.rest.creds.key_type if self.rest.creds else ''})")
            if was is False:
                self.alerts.push(Severity.INFO, "credential-ok", f"กุญแจ Testnet {key} ใช้ได้แล้ว worker กลับมาส่งคำสั่งได้")
        else:
            msg = data.get("msg") if isinstance(data, dict) else None
            self.alerts.push(Severity.CRITICAL, "credential", f"กุญแจ Testnet {key} ใช้ไม่ได้ จะไม่ส่งคำสั่งใดๆ ให้เช็กบน "
                             f"testnet.binance.vision ว่ากุญแจยังอยู่ (HTTP {status}{', ' + str(msg) if msg else ''})")

    def _ping(self) -> None:
        if self.deadman is None:
            return
        ok = self.deadman.ping()
        if self.state.deadman_ok is not False and not ok:
            self.alerts.push(Severity.WARNING, "deadman", "ส่งสัญญาณ 'ยังทำงานอยู่' ไป healthchecks.io ไม่สำเร็จ ครั้งเดียวไม่เป็นไร ถ้าเงียบนานเกินกำหนด "
                             "healthchecks จะส่งข้อความแยกมาบอกว่า worker เงียบ")
        self.state.deadman_ok = ok

    def _drain_feeds(self) -> None:
        if self.feeds is None:
            return
        while True:
            try:
                severity, key, text = self.feeds.events.get_nowait()
            except Exception:  # noqa: BLE001 - queue.Empty
                break
            self.alerts.push(severity, key, text)
        for severity, key, text in self.feeds.problems():
            self.alerts.push(severity, key, text)

    def _start_executor(self) -> None:
        ex, token = self.executor, self.state.lease_token
        if ex is None or ex.started or token is None:
            return
        try:
            ex.start(token)
        except Exception as e:  # noqa: BLE001 - retried next tick; orders stay blocked until it works
            self.alerts.push(Severity.WARNING, "executor", f"โหลดคำสั่งเดิมจากฐานข้อมูลไม่ได้ จะลองใหม่ ({type(e).__name__})")

    def _run_executor(self) -> None:
        ex, token = self.executor, self.state.lease_token
        if ex is None or token is None:
            return
        if not ex.started:
            self._start_executor()
            if not ex.started:
                return
        try:
            ex.tick(token, ready=not self.state.signing_paused and self.state.account_ok is True)
        except LeaseFenced as e:
            self.alerts.push(Severity.CRITICAL, "lease", "worker ตัวนี้หมดสิทธิ์เขียนแล้ว จึงหยุดตัวเอง ปกติเกิดตอน update หรือ restart ที่ตัวใหม่รับสิทธิ์ต่อไปแล้ว "
                             f"ถ้าตามด้วย 'worker เริ่มทำงาน' ก็ไม่ต้องทำอะไร ({e})")
            raise LeaseLost(str(e)) from e
        self.state.safety = ex.safety.state.value

    def _sync_bars(self) -> None:
        assert self.recorder is not None and self.feeds is not None
        try:
            for interval in ("1m", "1d"):
                n = self.recorder.sync_bars("BTCUSDT", interval, self.feeds.closed_bars(interval))
                if n:
                    self.log(f"stored {n} {interval} bars from the local files")
        except Exception as e:  # noqa: BLE001 - history can be sent again next start; the lease decides if the database is gone
            self.alerts.push(Severity.WARNING, "persist", f"บันทึกประวัติแท่งราคาไม่สำเร็จ จะลองใหม่ ({type(e).__name__})")

    def _persist(self) -> None:
        """Write queued feed records and the heartbeat. A failure keeps the records for the next try."""
        if self.recorder is None:
            return
        records: list[Any] = self.executor.drain_records() if self.executor is not None else []
        if self.feeds is not None:
            while True:
                try:
                    records.append(self.feeds.records.get_nowait())
                except Exception:  # noqa: BLE001 - queue.Empty
                    break
        s = self.state
        try:
            self.recorder.flush(records)
            self.recorder.store.heartbeat(self.cfg.owner, self.cfg.host, s.started_at, s.last_tick_at, self._body())
        except Exception as e:  # noqa: BLE001
            if self.persist_ok is not False:
                self.alerts.push(Severity.WARNING, "persist", f"เขียนข้อมูลลง Supabase ไม่ได้ หน้าเว็บอาจไม่อัปเดตชั่วคราว จะลองใหม่เอง ({type(e).__name__})")
            self.persist_ok = False
            return
        if self.persist_ok is False:
            self.alerts.push(Severity.INFO, "persist-ok", "เขียนข้อมูลลง Supabase ได้อีกแล้ว")
        self.persist_ok = True

    def _body(self) -> dict[str, Any]:
        s = self.state
        body = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        body |= {"pid": os.getpid(), "owner": self.cfg.owner, "mode": self.settings.trading_mode.value,
                 "execution_enabled": self.settings.testnet_execution_enabled}
        if self.feeds is not None:
            body["feeds"] = self.feeds.health()
        if self.executor is not None:
            body["execution"] = self.executor.body()
        if self.recorder is not None:
            body["persist"] = {"ok": self.persist_ok, "pending": len(self.recorder.pending), "epoch": self.recorder.epoch,
                               **self.recorder.counts}
        return body

    def _heartbeat(self) -> None:
        s = self.state
        s.last_tick_at = self.now().isoformat(timespec="seconds")
        body = self._body()
        path = self.cfg.heartbeat_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
        tmp.replace(path)
