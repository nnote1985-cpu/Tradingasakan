"""tn-r10a-code-v1: the R10a rule as the first Testnet strategy (testnet-development-plan.md §5.1, P5).

Once per UTC day the bot decides from the previous day's close, with the same
inputs and rule as the Shadow book pathA-r10a (frozen bd98dd8, unchanged):

- trend = ±2% band latch around the perp's SMA100 (``trading.strategies.sma_band``):
  in when the close is above SMA100 × 1.02, out when it is below SMA100 × 0.98;
- funding factor = 0.5 when the mean daily funding of the last 7 days is above
  0.0009, otherwise 1 (``funding_policy.v1``: archive prints, premium-index proxy
  after the last one);
- target BTC weight = trend × funding factor: 0, 0.5 or 1 of the book's equity.

Inputs come from data.binance.vision in a background thread, from 00:10 UTC
and every 30 minutes until the archive has the day; a tick never waits on
them. Since v1.1, a daily close the archive skipped before that day (the
archive has no 2026-10-28) is taken from the Binance futures REST API, so one
missing file cannot stop SMA100 for 100 days; the day being decided still comes
only from the archive, so the decision time is unchanged. Filled days are listed
in the snapshot (``rest_filled_days``). The decision and a snapshot of its inputs are stored once
(``trading.exec_strategy_decisions``). As in the Shadow book, an unchanged
target is not traded again; the bot also trades when the book's position does
not match the target (the first day, or after a HALT and a resume).

A trade is one MARKET order for the difference, through the normal gateway and
pre-trade checks, with up to 5 attempts if one ends without a fill. A buy
waits while entries are blocked; nothing is sent while the account is
RECOVERING, HALTING or HALTED, and Guardian (spot.tn.v1) watches the book.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent
from trading.execution.safety import SafetyState
from trading.execution.store import FEE_ALLOWANCE, ExecStore, IntentRefused
from trading.execution.types import OrderType, Side
from trading.runtime.alerts import Severity

if TYPE_CHECKING:
    from trading.runtime.executor import Executor

BOOK = "tn-r10a-code-v1"
RULES_VERSION = "tn-r10a-code-v1.1"        # pathA-r10a's rule at bd98dd8, on Testnet orders; v1.1 fills archive gaps
ALLOCATION = Decimal("2000")               # first soak (§12): one rebalance stays under the 2,000 USDT order cap
SYMBOL, BASE, QUOTE = "BTCUSDT", "BTC", "USDT"
TRY_FROM = dtime(0, 10)                     # UTC; the daily archive usually has the day some hours later
RETRY_EVERY_S = 1800.0
MAX_ATTEMPTS = 5
HISTORY_DAYS = 400                          # SMA100 needs 100 closes; the Shadow book loads 400
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"
TRADING = (SafetyState.RUNNING, SafetyState.SOFT_PAUSED, SafetyState.REDUCING)
ZERO = Decimal(0)
ACTION_TH = {"OPEN": "เปิดพอร์ต", "REBALANCE": "ปรับสัดส่วน", "HOLD": "เป้าเท่าเดิม ไม่เทรด", "NO_DECISION": "ข้อมูลไม่พอ คงสถานะเดิม"}


@dataclass(frozen=True)
class Decision:
    day: date
    weight: Decimal | None                  # None: an input was missing; the position is kept
    notes: str
    inputs: dict[str, Any]
    inputs_sha256: str
    data_ready_at: datetime


def _num(x: Any) -> float | None:
    return None if x is None or x != x else float(x)


def fill_gaps(daily: pd.DataFrame, day: date, rest: Callable[[date, date], pd.DataFrame]) -> tuple[pd.DataFrame, list[str]]:
    """Daily closes the archive skipped between its first row and the day before ``day``, taken from ``rest``.

    ``day`` itself is never filled: whether the day is decided still waits on the archive."""
    from trading.alerts.live_data import missing_days

    if not len(daily):
        return daily, []
    idx = pd.DatetimeIndex(daily.index)
    gaps = missing_days(idx, idx[0].date(), day - timedelta(days=1))
    if not gaps:
        return daily, []
    got = rest(date.fromisoformat(gaps[0]), date.fromisoformat(gaps[-1]))
    got = got[pd.DatetimeIndex(got.index).strftime("%Y-%m-%d").isin(gaps)]
    if not len(got):
        return daily, []
    out = pd.concat([daily, got]).sort_index()
    return out[~out.index.duplicated(keep="first")], [str(t.date()) for t in got.index]


def rest_daily_klines(start: date, end: date) -> pd.DataFrame:
    """Closed 1d perp klines for [start, end] from the futures REST API (public, no key)."""
    import urllib.parse

    from trading.market_data.binance_public import _get

    lo = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    hi = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000) - 1
    q = urllib.parse.urlencode({"symbol": SYMBOL, "interval": "1d", "startTime": lo, "endTime": hi, "limit": 1000})
    raw = _get(f"{REST_KLINES}?{q}", timeout=30)
    rows = json.loads(raw or b"[]")
    now_ms = datetime.now(UTC).timestamp() * 1000
    rows = [r for r in rows if r[6] < now_ms]                         # closed days only
    idx = pd.DatetimeIndex([pd.Timestamp(int(r[0]), unit="ms", tz="UTC") for r in rows], name="open_time")
    return pd.DataFrame({c: [float(r[i]) for r in rows] for i, c in enumerate(["open", "high", "low", "close", "volume"], 1)},
                        index=idx)


def decide(daily: pd.DataFrame, funding: pd.Series, est: pd.Series, day: date, data_ready_at: datetime,
           rest_filled: list[str] | None = None) -> Decision | None:
    """The R10a decision at ``day``'s close, or None when the data does not reach ``day`` yet."""
    from trading.alerts.live_data import FUNDING_POLICY
    from trading.alerts.risk_check import missing_inputs, signals
    from trading.strategies.sma_band import BAND, band_state

    sig = signals(daily, funding)
    if not len(sig) or sig.index[-1].date() < day:
        return None
    sig = sig[sig.index <= pd.Timestamp(day, tz="UTC")]
    state = band_state(sig["close"], signed=False)
    fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
    w = state.iloc[-1] * fund.iloc[-1]
    t = sig.iloc[-1]
    last = sig.index[-1]
    window = []
    for i in range(6, -1, -1):
        d = last - timedelta(days=i)
        window.append({"day": str(d.date()), "funding": _num(funding.get(d)),
                       "source": "proxy" if bool(est.get(d, False)) else ("actual" if d in funding.index else "missing")})
    if window[-1]["source"] == "missing":
        return None                       # the day's funding (print or premium-index proxy) is not out yet: wait
    proxy = sum(1 for x in window if x["source"] == "proxy")
    snap = {"close_perp": _num(t["close"]), "sma100": _num(t["sma100"]), "band": BAND, "band_state": _num(state.iloc[-1]),
            "funding_7d_per_day": _num(t["funding_7d_per_day"]), "funding_hot": None if t["funding_hot"] is None
            or t["funding_hot"] != t["funding_hot"] else bool(t["funding_hot"]), "suggested_btc_weight": _num(w),
            "funding_window": window, "funding_policy": FUNDING_POLICY["version"], "funding_proxy_days": proxy}
    if rest_filled:
        snap["rest_filled_days"] = rest_filled
    if w != w:
        weight, notes = None, ", ".join(missing_inputs(sig)) or "inputs missing"
    else:
        weight = Decimal(str(float(w)))
        sma = float(t["sma100"])
        notes = (f"close {float(t['close']):,.2f}, SMA100 {sma:,.2f}: in above {sma * (1 + BAND):,.2f}, out below "
                 f"{sma * (1 - BAND):,.2f}; {'holding' if state.iloc[-1] == 1 else 'out'}; funding x{fund.iloc[-1]:g}")
        if proxy:
            notes += f"; funding {proxy}/7 days are premium-index estimates"
    sha = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    return Decision(day, weight, notes, snap, sha, data_ready_at)


def fetch_decision(day: date) -> Decision | None:
    """Download the inputs (data.binance.vision, as the Shadow book) and decide. Runs off the tick thread."""
    from trading.alerts.live_data import daily_funding, daily_klines

    daily = daily_klines(SYMBOL, day - timedelta(days=HISTORY_DAYS), day)
    daily, filled = fill_gaps(daily, day, rest_daily_klines)
    funding, est = daily_funding(SYMBOL, day - timedelta(days=40), day)
    return decide(daily, funding, est, day, datetime.now(UTC), filled)


@dataclass
class R10aBot:
    store: ExecStore
    fetch: Callable[[date], Decision | None] = fetch_decision
    pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="r10a"))
    _future: Future[Decision | None] | None = None
    _future_day: date | None = None
    _next_try: float = 0.0
    _pending: Decision | None = None         # fetched, waiting for a price to compare the position with
    last_note: str = ""
    last: dict[str, Any] | None = None

    def step(self, ex: Executor, token: int) -> None:
        now = ex.now()
        day = now.date() - timedelta(days=1)
        row = self.store.load_decision(BOOK, day)
        if row is None:
            if now.time() >= TRY_FROM:
                self._decide(ex, token, day, now)
            row = self.store.load_decision(BOOK, day)
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
                ex.alerts.push(Severity.WARNING, "r10a-inputs", f"R10a โหลดข้อมูลราคาหรือ funding ไม่สำเร็จ จะลองใหม่ใน 30 นาที ({self.last_note})")
                return
            if d is None:
                self.last_note = f"the archive does not have {day} yet; trying again in 30 minutes"
                return
            self._record(ex, token, d, now)
            return
        if self._future is None and ex.mono() >= self._next_try:
            self._next_try = ex.mono() + RETRY_EVERY_S
            self._future, self._future_day = self.pool.submit(self.fetch, day), day
            self.last_note = f"loading inputs for {day}"

    def _record(self, ex: Executor, token: int, d: Decision, now: datetime) -> None:
        q, rules = ex.quote(), ex.rules
        previous = self.store.last_weight(BOOK, d.day)
        if d.weight is None:
            action = "NO_DECISION"
        elif previous is None:
            action = "OPEN"
        elif d.weight != previous:
            action = "REBALANCE"
        else:
            if q is None or rules is None:
                self._pending = d                                   # needs a price to compare the position with
                return
            action = "REBALANCE" if self._off_target(d.weight, q.bid, rules.min_notional) else "HOLD"
        notes = d.notes if action != "REBALANCE" or d.weight != previous else d.notes + "; position does not match the target"
        self._pending = None
        if self.store.add_decision(token, now, book=BOOK, rules_version=RULES_VERSION, day=d.day, weight=d.weight,
                                   previous_weight=previous, action=action, notes=notes, inputs=d.inputs,
                                   inputs_sha256=d.inputs_sha256, data_ready_at=d.data_ready_at):
            w = "–" if d.weight is None else f"{d.weight:.0%}"
            ex.alerts.push(Severity.INFO, "r10a-decision", f"R10a ตัดสินใจจากราคาปิดวันที่ {d.day}: {ACTION_TH[action]} "
                           f"เป้าถือ BTC {w} ({notes})")
            ex.log(f"r10a: {d.day} {action} weight {w}")

    def _off_target(self, weight: Decimal, bid: Decimal, min_notional: Decimal) -> bool:
        held, cash = self.store.position(BOOK, SYMBOL, BASE, QUOTE)
        equity = ALLOCATION + cash + held * bid
        return abs(weight * equity - held * bid) > max(min_notional, equity * Decimal("0.1"))

    # --- executing ---------------------------------------------------------------------------

    def _execute(self, ex: Executor, token: int, row: dict[str, Any]) -> None:
        if ex.safety.state not in TRADING or ex.rules is None:
            return
        if any(o.intent.book == BOOK and not o.is_terminal for o in ex.orders.values()) or \
                any(i.book == BOOK for _, i in ex.waiting.values()):
            return
        now, day = ex.now(), row["day"]
        if row["client_order_id"]:
            o = ex.lookup(row["client_order_id"])
            if o is not None and not o.is_terminal:
                return
            if o is not None and o.filled_qty > 0:
                self.store.finish_decision(token, now, BOOK, day, f"{o.intent.side.value} {o.filled_qty} BTC, {o.state.value}")
                ex.alerts.push(Severity.INFO, "r10a-trade", f"R10a {'ซื้อ' if o.intent.side is Side.BUY else 'ขาย'} {o.filled_qty} BTC "
                               f"แล้ว ตามคำตัดสินวันที่ {day} ({o.state.value})")
                return
            if row["attempts"] >= MAX_ATTEMPTS:
                self.store.finish_decision(token, now, BOOK, day, f"gave up after {MAX_ATTEMPTS} orders without a fill")
                ex.alerts.push(Severity.CRITICAL, "r10a-trade", f"R10a ส่งคำสั่ง {MAX_ATTEMPTS} ครั้งแล้วไม่ได้ fill เลย หยุดสำหรับวันที่ "
                               f"{day} book จะยังไม่ตรงเป้าจนกว่าจะมีคำตัดสินถัดไป ให้บอก Claude ใน thread")
                return
        q = ex.quote()
        if q is None:
            return
        rules = ex.rules
        weight = Decimal(row["weight"])
        held, cash = self.store.position(BOOK, SYMBOL, BASE, QUOTE)
        equity = ALLOCATION + cash + held * q.bid
        delta = weight * equity - held * q.bid
        if delta > 0:
            side, price = Side.BUY, q.ask
            cap = ex.limits.max_order_notional * Decimal("0.999")    # a larger gap is closed by the next decisions
            qty = rules.round_qty((min(delta, cap) / (q.ask * (1 + FEE_ALLOWANCE))).quantize(Decimal("1e-8"), ROUND_DOWN))
        else:
            side, price = Side.SELL, q.bid
            qty = rules.round_qty(max(held, ZERO) if weight == 0 else min(max(held, ZERO), -delta / q.bid))
        if qty < rules.min_qty or qty * price < rules.min_notional:
            self.store.finish_decision(token, now, BOOK, day, "already at the target (the difference is below the exchange minimum)")
            return
        if side is Side.BUY and not ex.safety.allows_entries:
            return                                                 # waits for RUNNING
        n = int(row["attempts"]) + 1
        intent = OrderIntent(mode=TradingMode.TESTNET, book=BOOK, decision_id=f"r10a-{day}", leg=f"rebalance-{n}", symbol=SYMBOL,
                             side=side, order_type=OrderType.MARKET, qty=qty, limit_price=None, created_at=now,
                             safety_epoch=ex.safety.epoch, reduce_only=side is Side.SELL)
        try:
            # The budget is the book's equity, so gains compound and losses shrink it, as in the Shadow book.
            self.store.accept_intent(intent, unit_price=price, budget=max(equity, ZERO), base=BASE)
        except IntentRefused as e:
            self.last_note = f"{day} {side.value} {qty} not accepted: {e}"
            if str(e) != "ENTRIES_BLOCKED_" + ex.safety.state.value:
                ex.alerts.push(Severity.WARNING, "r10a-trade", f"R10a ส่งคำสั่งไม่ได้ ({self.last_note})")
            return
        self.store.decision_attempt(token, now, BOOK, day, n, intent.client_order_id)
        self.last_note = f"{day}: {side.value} {qty} BTC sent (attempt {n})"

    # --- status ------------------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        r = self.last
        return {"book": BOOK, "allocation": str(ALLOCATION), "note": self.last_note,
                "decision": None if r is None else {"day": str(r["day"]), "weight": None if r["weight"] is None else str(r["weight"]),
                                                    "action": r["action"], "attempts": r["attempts"], "outcome": r["outcome"]}}

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

