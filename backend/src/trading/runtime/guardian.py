"""Guardian: intraday loss limits and HALT for Testnet books (testnet-development-plan.md §8.5, P5).

The executor calls :meth:`Guardian.tick` every tick, under the same writer
lease, before strategies get a turn. Guardian never waits for a strategy or an
LLM. One tick:

1. carries out operator requests: HALT (a named person's kill switch, also the
   HALT drill) and RESUME (only from HALTED; the account goes back through
   RECOVERING, and every book's high-water mark restarts from its equity then);
2. on a fresh quote, marks each guarded book at the bid: its allocation plus
   the cash its own fills moved, plus the BTC it holds. ``spot.tn.v1`` (the
   ``spot.v1`` levels Note chose) alerts at a 5% loss in one UTC day or a 15%
   drawdown and HALTs at a 25% drawdown from the high-water mark;
3. while HALTING: cancels every open buy, sells what each guarded book holds
   (MARKET, through the normal gateway on the new safety epoch, so nothing the
   old epoch queued can add exposure), keeps doing so for late fills, and moves
   to HALTED only once no buy is open, nothing is unresolved and every guarded
   book holds less than the exchange minimum. An HTTP answer is never taken as
   flat on its own.

HALT is account-wide and latched: midnight or a restart never clears it, and
every alert, HALT, flat confirmation and resume is kept in
``trading.exec_risk_events``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent, client_order_id
from trading.execution.safety import IllegalSafetyTransition, SafetyState
from trading.execution.store import ExecStore, IntentRefused
from trading.execution.types import OrderType, Side
from trading.risk.profiles import SPOT_TN_V1
from trading.risk.sizing import RiskProfile
from trading.runtime.alerts import Severity

if TYPE_CHECKING:
    from trading.runtime.executor import Executor

PROFILE_NAME = "spot.tn.v1"
SAVE_EVERY_S = 60.0
MAX_REDUCE_LEGS = 50
QUOTE_MAX_AGE = timedelta(seconds=5)         # the quote freshness limit orders use (plan §8.2)
HALT_DECISION = "halt-e"
ZERO = Decimal(0)


@dataclass(frozen=True)
class GuardedBook:
    book: str
    allocation: Decimal                      # USDT the book started with
    symbol: str = "BTCUSDT"
    base: str = "BTC"
    quote: str = "USDT"


@dataclass
class _Mark:
    hwm: Decimal
    hwm_at: datetime
    day: date
    day_open: Decimal
    equity: Decimal = ZERO
    saved_mono: float = -1e18
    day_alerted: bool = False
    dd_alerted: bool = False


@dataclass
class Guardian:
    store: ExecStore
    books: list[GuardedBook]
    profile: RiskProfile = SPOT_TN_V1
    marks: dict[str, _Mark] = field(default_factory=dict)
    reset_hwm: bool = False                  # set by a resume: the next mark restarts every high-water mark
    last_note: str = ""

    # --- entry point ------------------------------------------------------------------

    def tick(self, ex: Executor, token: int) -> None:
        self._requests(ex, token)
        q = ex.quote()
        if q is None or ex.rules is None or ex.now() - datetime.fromtimestamp(q.received_ms / 1000, UTC) > QUOTE_MAX_AGE:
            return                                                    # marks wait for a fresh quote; HALT latches and requests do not
        for b in self.books:
            self._mark(ex, b, q.bid)
        if ex.safety.state is SafetyState.HALTING:
            self._reduce(ex, token, q.bid)

    # --- operator requests -------------------------------------------------------------

    def _requests(self, ex: Executor, token: int) -> None:
        for rid, kind, actor, reason in self.store.open_requests():
            now = ex.now()
            if kind == "HALT":
                outcome = self.halt(ex, f"operator {actor}: {reason}", book=None)
            elif ex.safety.state is SafetyState.HALTED:
                ex.safety.operator_resume(now, actor)
                ex.store.save_safety(ex.safety)
                self.reset_hwm = True
                self.store.add_risk_event(None, "RESUME", f"{actor}: {reason}", ex.safety.epoch, now)
                ex.alerts.push(Severity.INFO, "guardian-resume", f"resumed by {actor} ({reason}); entries start again once the account "
                               "check is MATCH")
                outcome = "resumed; RECOVERING until the account check is MATCH"
            else:
                outcome = f"refused: account is {ex.safety.state.value}, resume needs HALTED (flat confirmed)"
            self.store.finish_request(rid, token, now, outcome)
            ex.log(f"guardian: {kind} request {rid} by {actor}: {outcome}")

    def halt(self, ex: Executor, reason: str, *, book: str | None, equity: Decimal | None = None,
             hwm: Decimal | None = None) -> str:
        """Latch HALT (idempotent) and raise the safety epoch; the next ticks cancel buys and reduce."""
        if ex.safety.state in (SafetyState.HALTING, SafetyState.HALTED):
            return f"already {ex.safety.state.value}"
        now = ex.now()
        try:
            ex.safety.halt(now, reason)
        except IllegalSafetyTransition as e:              # pragma: no cover - every non-halted state may halt
            return f"refused: {e}"
        ex.store.save_safety(ex.safety)
        ex.paused_by_us = False
        self.store.add_risk_event(book, "HALT", reason, ex.safety.epoch, now, equity, hwm)
        ex.alerts.push(Severity.CRITICAL, "guardian-halt", f"HALT: {reason}. New entries are blocked; open buys are being canceled "
                       "and the guarded books sold. A resume needs a named operator.")
        return f"halted at epoch {ex.safety.epoch}"

    # --- marks and limits ----------------------------------------------------------------

    def equity(self, b: GuardedBook, bid: Decimal) -> tuple[Decimal, Decimal]:
        """(equity at ``bid``, base held) for book ``b``."""
        held, cash = self.store.position(b.book, b.symbol, b.base, b.quote)
        return b.allocation + cash + held * bid, held

    def _mark(self, ex: Executor, b: GuardedBook, bid: Decimal) -> None:
        now = ex.now()
        equity, _ = self.equity(b, bid)
        m = self.marks.get(b.book)
        if m is None:
            row = self.store.load_book_risk(b.book)
            m = _Mark(equity, now, now.date(), equity) if row is None else _Mark(Decimal(row[0]), row[1], row[2], Decimal(row[3]))
            self.marks[b.book] = m
        changed = False
        if self.reset_hwm:
            m.hwm, m.hwm_at, m.dd_alerted, changed = equity, now, False, True
        if now.date() != m.day:
            m.day, m.day_open, m.day_alerted, changed = now.date(), equity, False, True
        if equity > m.hwm:
            m.hwm, m.hwm_at, changed = equity, now, True
        m.equity = equity
        dd = 1 - equity / m.hwm if m.hwm > 0 else ZERO
        day_loss = 1 - equity / m.day_open if m.day_open > 0 else ZERO
        self.last_note = f"{b.book}: equity {equity:.2f}, drawdown {dd:.2%}, day {-day_loss:+.2%}"
        if day_loss >= self.profile.daily_loss_trigger_fraction and not m.day_alerted:
            m.day_alerted = True
            text = f"{b.book} is down {day_loss:.1%} today (equity {equity:.2f} USDT, day open {m.day_open:.2f}); alert only"
            self.store.add_risk_event(b.book, "DAY_LOSS_ALERT", text, ex.safety.epoch, now, equity, m.hwm)
            ex.alerts.push(Severity.WARNING, f"guardian-day-{b.book}", text)
        alert_at = self.profile.drawdown_alert_fraction
        if alert_at is not None and dd >= alert_at and not m.dd_alerted:
            m.dd_alerted = True
            text = f"{b.book} drawdown {dd:.1%} from the high of {m.hwm:.2f} USDT (equity {equity:.2f}); alert only"
            self.store.add_risk_event(b.book, "DRAWDOWN_ALERT", text, ex.safety.epoch, now, equity, m.hwm)
            ex.alerts.push(Severity.WARNING, f"guardian-dd-{b.book}", text)
        elif alert_at is not None and dd < alert_at:
            m.dd_alerted = False
        halt_at = self.profile.portfolio_drawdown_halt_fraction
        if halt_at is not None and dd >= halt_at:
            self.halt(ex, f"{b.book} drawdown {dd:.1%} reached the {halt_at:.0%} {PROFILE_NAME} limit", book=b.book,
                      equity=equity, hwm=m.hwm)
        if changed or ex.mono() - m.saved_mono >= SAVE_EVERY_S:
            self.store.save_book_risk(b.book, PROFILE_NAME, b.allocation, m.hwm, m.hwm_at, m.day, m.day_open, equity, now)
            m.saved_mono = ex.mono()
        if b is self.books[-1]:
            self.reset_hwm = False

    # --- HALTING: cancel buys, reduce, confirm flat --------------------------------------

    def _reduce(self, ex: Executor, token: int, bid: Decimal) -> None:
        assert ex.rules is not None
        rules, now = ex.rules, ex.now()
        # Every open order except our own reduce sells goes: buys add exposure, and other sells lock the BTC we need to sell.
        for o in list(ex.orders.values()):
            if o.is_open and not o.intent.decision_id.startswith(HALT_DECISION):
                ex.cancel(o.client_order_id, token, emergency=True)
        busy = bool(ex.unresolved()) or any(not o.is_terminal and not o.intent.decision_id.startswith(HALT_DECISION)
                                            for o in ex.orders.values()) \
            or any(not i.decision_id.startswith(HALT_DECISION) for _, i in ex.waiting.values())
        if busy:
            return                                                    # sell once nothing else can move the position
        flat = True
        for b in self.books:
            _, held = self.equity(b, bid)
            qty = rules.round_qty(max(held, ZERO))
            if qty < rules.min_qty or qty * bid < rules.min_notional:
                continue                                              # dust the exchange will not take
            flat = False
            self._sell(ex, b, qty, bid, now)
        if flat and not any(not o.is_terminal for o in ex.orders.values()) and not ex.waiting:
            ex.safety.halted_flat_confirmed(now)
            ex.store.save_safety(ex.safety)
            self.store.add_risk_event(None, "FLAT_CONFIRMED", "no open orders, nothing unresolved, guarded books below the exchange "
                                      "minimum", ex.safety.epoch, now)
            ex.alerts.push(Severity.CRITICAL, "guardian-halt", "HALTED: the guarded books are flat on the Testnet and no order is open. "
                           "Entries stay blocked until a named operator resumes.")

    def _sell(self, ex: Executor, b: GuardedBook, qty: Decimal, bid: Decimal, now: datetime) -> None:
        """Queue one MARKET SELL of ``qty`` for ``b`` on the current epoch, unless one is already on its way."""
        decision = f"{HALT_DECISION}{ex.safety.epoch}"
        for n in range(1, MAX_REDUCE_LEGS + 1):
            cid = client_order_id(b.book, decision, f"reduce-{n}")
            o = ex.lookup(cid)
            if o is None:
                break
            if not o.is_terminal:
                return                                                # the last one is still working
        else:
            ex.alerts.push(Severity.CRITICAL, f"guardian-reduce-{b.book}", f"{b.book}: {MAX_REDUCE_LEGS} reduce orders did not make "
                           "it flat; stopped sending, check the Testnet by hand")
            return
        intent = OrderIntent(mode=TradingMode.TESTNET, book=b.book, decision_id=decision, leg=f"reduce-{n}", symbol=b.symbol,
                             side=Side.SELL, order_type=OrderType.MARKET, qty=qty, limit_price=None, created_at=now,
                             safety_epoch=ex.safety.epoch, reduce_only=True)
        try:
            if not self.store.accept_intent(intent, unit_price=bid, budget=b.allocation, base=b.base):
                raise IntentRefused(f"client order id {intent.client_order_id} is taken by another account")
        except IntentRefused as e:
            ex.alerts.push(Severity.CRITICAL, f"guardian-reduce-{b.book}", f"{b.book}: reduce order refused: {e}")

    # --- status ------------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for b in self.books:
            m = self.marks.get(b.book)
            if m is None:
                out.append({"book": b.book, "allocation": str(b.allocation), "equity": None})
                continue
            dd = 1 - m.equity / m.hwm if m.hwm > 0 else ZERO
            out.append({"book": b.book, "allocation": str(b.allocation), "equity": f"{m.equity:.2f}", "hwm": f"{m.hwm:.2f}",
                        "drawdown": f"{dd:.4f}", "day_open": f"{m.day_open:.2f}"})
        return {"profile": PROFILE_NAME, "books": out}

