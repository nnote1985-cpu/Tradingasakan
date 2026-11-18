"""The futures desk worker: sync of the Futures Demo account (futures-desk-plan.md §5) and, with an
:class:`~trading.futures.executor.Executor`, manual order requests from the web page (§7).

Runs as its own process (systemd ``tradingasakan-futures``), apart from the spot
workers, so starting, stopping or crashing it never touches the running bots.
Every ``sync_every_s`` it reads balances, positions, open orders (regular and
algo), new fills and new income, updates the high-water mark and day open, and
writes it all in one transaction. It holds the writer lease for its account so
two copies never write at once. Without an executor it sends no orders. With one, it checks
for queued requests every tick and syncs at once when there is one, so a ticket is acted on
within a few seconds and on fresh data.

It alerts (Telegram, when set) when a position has no stop-loss on the exchange,
which breaks the desk rule, and when a loss cap is reached.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from trading.futures import rules
from trading.futures.executor import Executor
from trading.futures.rest import FuturesDemoRest
from trading.futures.risk import caps
from trading.futures.sync import RiskState, Snapshot, fill_row, income_row, mark_risk, markets, snapshot
from trading.runtime.alerts import AlertQueue, Severity

LOOKBACK = timedelta(days=7)   # how far back the first run reads fills and income


class LeaseLost(RuntimeError):
    pass


class Store(Protocol):
    def acquire_lease(self, owner: str, now: datetime, ttl: timedelta) -> Any: ...
    def load_risk(self) -> RiskState | None: ...
    def last_fill_ids(self) -> dict[str, int]: ...
    def last_income_at(self) -> datetime | None: ...
    def save(self, snap: Snapshot, risk: RiskState, body: dict[str, Any], fills: list[dict[str, Any]],
             income: list[dict[str, Any]]) -> tuple[int, int]: ...
    def open_requests(self) -> list[dict[str, Any]]: ...


@dataclass
class Config:
    owner: str
    host: str = ""
    sync_every_s: float = 10.0
    lease_ttl: timedelta = timedelta(seconds=45)
    clock_every_s: float = 600.0
    info_every_s: float = 3600.0
    config_every_s: float = 60.0


@dataclass
class State:
    started_at: str = ""
    last_sync_at: str | None = None
    last_error: str | None = None
    lease_token: int | None = None
    clock_offset_ms: float | None = None
    syncs: int = 0
    fills_added: int = 0
    income_added: int = 0
    _due: dict[str, float] = field(default_factory=dict)


class FuturesWorker:
    def __init__(self, rest: FuturesDemoRest, store: Store, alerts: AlertQueue, cfg: Config, *,
                 executor: Executor | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), monotonic: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print) -> None:
        self.rest, self.store, self.alerts, self.cfg = rest, store, alerts, cfg
        self.executor = executor
        self.now, self.mono, self.log = now, monotonic, log
        self.state = State()
        self._info: Any = None
        self._configs: dict[str, dict[str, Any]] = {}
        self._risk: RiskState | None = None
        self._unprotected: set[str] = set()
        self._cap_state: str = "OK"

    def _due(self, name: str, every: float) -> bool:
        t = self.mono()
        if t >= self.state._due.get(name, t):
            self.state._due[name] = t + every
            return True
        return False

    def _lease(self) -> None:
        lease = self.store.acquire_lease(self.cfg.owner, self.now(), self.cfg.lease_ttl)
        if lease is None:
            raise LeaseLost("writer lease held by another futures worker")
        if self.state.lease_token is not None and lease.token != self.state.lease_token:
            raise LeaseLost(f"lease token changed {self.state.lease_token} -> {lease.token}")
        self.state.lease_token = lease.token

    def start(self) -> None:
        self.state.started_at = self.now().isoformat(timespec="seconds")
        self._lease()
        self._risk = self.store.load_risk()
        mode = self.rest.get("/fapi/v1/positionSide/dual")
        if isinstance(mode, dict) and mode.get("dualSidePosition") is True:
            self.alerts.push(Severity.WARNING, "fut-mode", "บัญชี Futures Demo ตั้งเป็น Hedge mode หน้าเว็บอ่านได้แต่สรุปไม้จะผิด "
                             "ให้เปลี่ยนเป็น One-way mode ในหน้า Preferences ของ demo.binance.com")
        self.alerts.push(Severity.INFO, "fut-start", "worker Futures Demo เริ่มทำงาน " + (
            "(รับคำสั่งจากหน้าเว็บ)" if self.executor else "(อ่านอย่างเดียว ไม่ส่งคำสั่ง)"))

    def tick(self) -> None:
        self._lease()
        queued = self.executor is not None and bool(self.store.open_requests())
        if not self._due("sync", self.cfg.sync_every_s) and not queued:
            return
        if queued:
            self.state._due["sync"] = self.mono() + self.cfg.sync_every_s
        try:
            self.sync()
            self.state.last_error = None
        except LeaseLost:
            raise
        except Exception as e:  # noqa: BLE001 - one failed sync is retried next round; the page shows how old the data is
            msg = f"{type(e).__name__}: {e}"
            if self.state.last_error is None:
                self.alerts.push(Severity.WARNING, "fut-sync", f"อ่านบัญชี Futures Demo ไม่สำเร็จ จะลองใหม่เอง ({msg[:200]})")
            self.state.last_error = msg[:500]
            self.log(f"sync failed: {msg}")

    def run(self, should_stop: Callable[[], bool], sleep: Callable[[float], None] = time.sleep, tick_s: float = 2.0) -> None:
        self.start()
        while not should_stop():
            self.tick()
            sleep(tick_s)
        self.alerts.flush()

    # --- one sync -------------------------------------------------------------

    def sync(self) -> None:
        rest, now = self.rest, self.now()
        if self._due("clock", self.cfg.clock_every_s):
            self.state.clock_offset_ms = round(rest.sync_clock(), 1)
        if self._info is None or self._due("info", self.cfg.info_every_s):
            self._info = rest.exchange_info()
        if not self._configs or self._due("config", self.cfg.config_every_s):
            cfgs = rest.get("/fapi/v1/symbolConfig")
            self._configs = {c["symbol"]: c for c in cfgs or [] if isinstance(c, dict)}
        snap = snapshot(now, rest.account(), rest.position_risk(), rest.open_orders(), rest.open_algo_orders(),
                        markets(self._info, rest.premium_index()), self._configs)
        fills = self._new_fills(now)
        income = self._new_income(now)
        self._risk = mark_risk(self._risk, snap.margin_balance, now)
        self.state.syncs += 1
        self.state.last_sync_at = now.isoformat(timespec="seconds")
        nf, ni = self.store.save(snap, self._risk, self.body(), fills, income)
        self.state.fills_added += nf
        self.state.income_added += ni
        self._check_rules(snap)
        if self.executor is not None:
            self.executor.run(snap, self._risk, now)

    def _new_fills(self, now: datetime) -> list[dict[str, Any]]:
        last = self.store.last_fill_ids()
        out: list[dict[str, Any]] = []
        for sym in rules.SYMBOLS:
            from_id = last[sym] + 1 if sym in last else None
            start = None if from_id is not None else int((now - LOOKBACK).timestamp() * 1000)
            while True:
                page = self.rest.user_trades(sym, from_id, start) or []
                out += [fill_row(t) for t in page]
                if len(page) < 1000:
                    break
                from_id, start = int(page[-1]["id"]) + 1, None
        return out

    def _new_income(self, now: datetime) -> list[dict[str, Any]]:
        last = self.store.last_income_at()
        start = int(((last + timedelta(milliseconds=1)) if last else now - LOOKBACK).timestamp() * 1000)
        out: list[dict[str, Any]] = []
        while True:
            page = self.rest.income(start) or []
            out += [income_row(i) for i in page]
            if len(page) < 1000:
                break
            start = int(page[-1]["time"]) + 1
        return out

    def _check_rules(self, snap: Snapshot) -> None:
        protected = {o["symbol"] for o in snap.open_orders if o["kind"] == "SL"}
        bare = {p["symbol"] for p in snap.positions} - protected
        for sym in sorted(bare - self._unprotected):
            self.alerts.push(Severity.CRITICAL, f"fut-nosl-{sym}", f"position {sym} บน Futures Demo ไม่มี Stop-loss บน exchange "
                             "ผิดกฎของหน้า Futures ให้ตั้ง SL หรือปิด position")
        self._unprotected = bare
        assert self._risk is not None
        c = caps(snap.margin_balance, self._risk)
        worst = "BLOCKED" if c["blocked"] else "OK"
        if worst != self._cap_state:
            if worst == "BLOCKED":
                self.alerts.push(Severity.CRITICAL, "fut-cap", f"ขาดทุนถึงเพดานแล้ว ({'; '.join(c['reasons'])}) ห้ามเปิดไม้ใหม่")
            else:
                self.alerts.push(Severity.INFO, "fut-cap-ok", "เพดานขาดทุนกลับมาปกติ เปิดไม้ใหม่ได้")
        self._cap_state = worst

    def body(self) -> dict[str, Any]:
        s = self.state
        out: dict[str, Any] = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        out |= {"owner": self.cfg.owner, "host": self.cfg.host, "orders_enabled": self.executor is not None,
                "rules": {"risk_per_trade": str(rules.RISK_PER_TRADE), "day_loss_cap": str(rules.DAY_LOSS_CAP),
                          "total_loss_cap": str(rules.TOTAL_LOSS_CAP)}}
        return out
