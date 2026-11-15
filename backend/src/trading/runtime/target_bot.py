"""One strategy on one Spot Testnet book, through the strategy contract and the order planner (M3 §3.3).

``TargetBot`` is the daily state machine of ``R10aBot`` (tn-r10a-code-v1.1) with the two parts that
were tied to R10a taken out: the decision comes from any :class:`~trading.contracts.strategy.Strategy`
and the order size from :func:`~trading.execution.planner.plan_spot`. Everything else is the same:

- from 00:10 UTC the bot asks for the previous day's decision in a background thread, every 30
  minutes until the strategy has it; a tick never waits;
- the decision is stored once (``trading.exec_strategy_decisions``): OPEN (first target), REBALANCE
  (new target, or the position is more than 10% of equity off an unchanged one), HOLD, or NO_DECISION
  (an input was missing; the position is kept);
- a trade is one MARKET order per planned leg through the normal gateway, at most 5 attempts without
  a fill; a buy waits while entries are blocked; nothing is sent while RECOVERING, HALTING or HALTED.

It has the same ``step``/``body``/``close`` interface as ``R10aBot`` so M4 can attach it to the
executor without changing the executor. Nothing in the running worker uses it yet.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trading.contracts.enums import TradingMode
from trading.contracts.strategy import Market, Strategy, TargetDecision
from trading.execution import planner
from trading.execution.intent import OrderIntent
from trading.execution.safety import SafetyState
from trading.execution.store import ExecStore, IntentRefused
from trading.execution.types import OrderType, Side
from trading.runtime.alerts import Severity

if TYPE_CHECKING:
    from trading.runtime.executor import Executor

TRY_FROM = dtime(0, 10)
RETRY_EVERY_S = 1800.0
MAX_ATTEMPTS = 5
TRADING = (SafetyState.RUNNING, SafetyState.SOFT_PAUSED, SafetyState.REDUCING)
ZERO = Decimal(0)
ACTION_TH = {"OPEN": "เปิดพอร์ต", "REBALANCE": "ปรับสัดส่วน", "HOLD": "เป้าเท่าเดิม ไม่เทรด", "NO_DECISION": "ข้อมูลไม่พอ คงสถานะเดิม"}
OUTCOME_BELOW_MIN = "already at the target (the difference is below the exchange minimum)"


def _split(symbol: str) -> tuple[str, str]:
    for quote in ("USDT", "USDC", "FDUSD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    raise ValueError(f"cannot split {symbol} into base and quote")


@dataclass
class TargetBot:
    store: ExecStore
    strategy: Strategy
    book: str
    allocation: Decimal
    label: str                              # name in alerts
    tag: str                                # decision_id prefix, alert keys, log lines
    fetch: Callable[[date], TargetDecision | None] | None = None   # default: strategy.load + strategy.decide
    pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="target"))
    _future: Future[TargetDecision | None] | None = None
    _future_day: date | None = None
    _next_try: float = 0.0
    _pending: TargetDecision | None = None
    last_note: str = ""
    last: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.strategy.market is not Market.SPOT:
            raise ValueError("TargetBot runs Spot books only; Perp execution is M5 (platform plan §4)")
        self.base, self.quote_asset = _split(self.strategy.instrument)

    @property
    def rules_version(self) -> str:
        return self.strategy.version

    def _fetch(self, day: date) -> TargetDecision | None:
        if self.fetch is not None:
            return self.fetch(day)
        inputs = self.strategy.load(day)
        return self.strategy.decide(inputs, day, datetime.now(UTC))

    def step(self, ex: Executor, token: int) -> None:
        now = ex.now()
        day = now.date() - timedelta(days=1)
        row = self.store.load_decision(self.book, day)
        if row is None:
            if now.time() >= TRY_FROM:
                self._decide(ex, token, day, now)
            row = self.store.load_decision(self.book, day)
        self.last = row
        if row is not None and row["executed_at"] is None and row["action"] in ("OPEN", "REBALANCE"):
            self._execute(ex, token, row)

    # --- deciding ------------------------------------------------------------------------

    def _decide(self, ex: Executor, token: int, day: date, now: datetime) -> None:
        if self._pending is not None and self._pending.day == day:
            self._record(ex, token, self._pending, now)
            return
        if self._future is not None and self._future_day == day and self._future.done():
            fut, self._future = self._future, None
            try:
                d = fut.result()
            except Exception as e:  # noqa: BLE001 - a download error waits for the next try
                self.last_note = f"inputs for {day} failed: {type(e).__name__}: {e}"[:300]
                ex.alerts.push(Severity.WARNING, f"{self.tag}-inputs",
                               f"{self.label} โหลดข้อมูลไม่สำเร็จ จะลองใหม่ใน 30 นาที ({self.last_note})")
                return
            if d is None:
                self.last_note = f"the data does not have {day} yet; trying again in 30 minutes"
                return
            if d.day != day or d.instrument != self.strategy.instrument or d.strategy_version != self.rules_version:
                self.last_note = f"{self.strategy.version} answered for {d.strategy_version} {d.instrument} {d.day}; ignored"
                return
            self._record(ex, token, d, now)
            return
        if self._future is None and ex.mono() >= self._next_try:
            self._next_try = ex.mono() + RETRY_EVERY_S
            self._future, self._future_day = self.pool.submit(self._fetch, day), day
            self.last_note = f"loading inputs for {day}"

    def _record(self, ex: Executor, token: int, d: TargetDecision, now: datetime) -> None:
        q, rules = ex.quote(), ex.rules
        previous = self.store.last_weight(self.book, d.day)
        w = d.target_weight
        if w is None:
            action = "NO_DECISION"
        elif previous is None:
            action = "OPEN"
        elif w != previous:
            action = "REBALANCE"
        else:
            if q is None or rules is None:
                self._pending = d                                   # needs a price to compare the position with
                return
            held, cash = self.store.position(self.book, self.strategy.instrument, self.base, self.quote_asset)
            equity = planner.spot_equity(self.allocation, cash, held, q.bid)
            action = "REBALANCE" if planner.off_target(w, held, equity, q.bid, rules.min_notional) else "HOLD"
        notes = d.reason if action != "REBALANCE" or w != previous else d.reason + "; position does not match the target"
        self._pending = None
        if self.store.add_decision(token, now, book=self.book, rules_version=self.rules_version, day=d.day, weight=w,
                                   previous_weight=previous, action=action, notes=notes, inputs=dict(d.inputs),
                                   inputs_sha256=d.inputs_sha256, data_ready_at=d.data_ready_at):
            ws = "–" if w is None else f"{w:.0%}"
            ex.alerts.push(Severity.INFO, f"{self.tag}-decision", f"{self.label} ตัดสินใจจากราคาปิดวันที่ {d.day}: {ACTION_TH[action]} "
                           f"เป้าถือ {self.base} {ws} ({notes})")
            ex.log(f"{self.tag}: {d.day} {action} weight {ws}")

    # --- executing ---------------------------------------------------------------------------

    def _execute(self, ex: Executor, token: int, row: dict[str, Any]) -> None:
        if ex.safety.state not in TRADING or ex.rules is None:
            return
        if any(o.intent.book == self.book and not o.is_terminal for o in ex.orders.values()) or \
                any(i.book == self.book for _, i in ex.waiting.values()):
            return
        now, day = ex.now(), row["day"]
        if row["client_order_id"]:
            o = ex.lookup(row["client_order_id"])
            if o is not None and not o.is_terminal:
                return
            if o is not None and o.filled_qty > 0:
                self.store.finish_decision(token, now, self.book, day, f"{o.intent.side.value} {o.filled_qty} {self.base}, {o.state.value}")
                ex.alerts.push(Severity.INFO, f"{self.tag}-trade", f"{self.label} {'ซื้อ' if o.intent.side is Side.BUY else 'ขาย'} "
                               f"{o.filled_qty} {self.base} แล้ว ตามคำตัดสินวันที่ {day} ({o.state.value})")
                return
            if row["attempts"] >= MAX_ATTEMPTS:
                self.store.finish_decision(token, now, self.book, day, f"gave up after {MAX_ATTEMPTS} orders without a fill")
                ex.alerts.push(Severity.CRITICAL, f"{self.tag}-trade", f"{self.label} ส่งคำสั่ง {MAX_ATTEMPTS} ครั้งแล้วไม่ได้ fill เลย "
                               f"หยุดสำหรับวันที่ {day} book จะยังไม่ตรงเป้าจนกว่าจะมีคำตัดสินถัดไป ให้บอก Claude ใน thread")
                return
        q = ex.quote()
        if q is None:
            return
        held, cash = self.store.position(self.book, self.strategy.instrument, self.base, self.quote_asset)
        p = planner.plan_spot(Decimal(row["weight"]), allocation=self.allocation, cash=cash, held=held, bid=q.bid, ask=q.ask,
                              rules=ex.rules, max_order_notional=ex.limits.max_order_notional)
        if p.empty:
            outcome = OUTCOME_BELOW_MIN if p.reason == planner.BELOW_MIN else f"not traded: {p.reason}"
            self.store.finish_decision(token, now, self.book, day, outcome)
            if p.reason != planner.BELOW_MIN:
                ex.alerts.push(Severity.WARNING, f"{self.tag}-trade", f"{self.label} ไม่ส่งคำสั่งวันที่ {day} ({p.reason})")
            return
        leg = p.legs[0]                                             # Spot plans have one leg
        if leg.side is Side.BUY and not ex.safety.allows_entries:
            return                                                  # waits for RUNNING
        n = int(row["attempts"]) + 1
        intent = OrderIntent(mode=TradingMode.TESTNET, book=self.book, decision_id=f"{self.tag}-{day}", leg=f"rebalance-{n}",
                             symbol=self.strategy.instrument, side=leg.side, order_type=OrderType.MARKET, qty=leg.qty,
                             limit_price=None, created_at=now, safety_epoch=ex.safety.epoch, reduce_only=leg.reduce_only)
        equity = planner.spot_equity(self.allocation, cash, held, q.bid)
        try:
            self.store.accept_intent(intent, unit_price=leg.price, budget=max(equity, ZERO), base=self.base)
        except IntentRefused as e:
            self.last_note = f"{day} {leg.side.value} {leg.qty} not accepted: {e}"
            if str(e) != "ENTRIES_BLOCKED_" + ex.safety.state.value:
                ex.alerts.push(Severity.WARNING, f"{self.tag}-trade", f"{self.label} ส่งคำสั่งไม่ได้ ({self.last_note})")
            return
        self.store.decision_attempt(token, now, self.book, day, n, intent.client_order_id)
        self.last_note = f"{day}: {leg.side.value} {leg.qty} {self.base} sent (attempt {n})"

    # --- status ------------------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        r = self.last
        return {"book": self.book, "strategy": self.strategy.version, "allocation": str(self.allocation), "note": self.last_note,
                "decision": None if r is None else {"day": str(r["day"]), "weight": None if r["weight"] is None else str(r["weight"]),
                                                    "action": r["action"], "attempts": r["attempts"], "outcome": r["outcome"]}}

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def r10a_v2_bot(store: ExecStore, **kw: Any) -> TargetBot:
    """tn-r10a-code-v2 with v1.1's allocation (2,000 USDT). Not started by the worker in M3."""
    from trading.strategies.r10a_v2 import R10aV2

    return TargetBot(store, R10aV2(), book="tn-r10a-code-v2", allocation=Decimal("2000"), label="R10a v2", tag="r10a2", **kw)
