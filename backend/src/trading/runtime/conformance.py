"""tn-conformance-v1: a scheduled order profile that exercises the real Testnet lifecycle (testnet-development-plan.md §5.4).

It is not a strategy and its results are not strategy P&L. Every 4 hours (UTC
00, 04, 08, ...) one cycle runs these legs in order, each waiting for the one
before it to end:

1. ``mkt-buy``: MARKET BUY worth about 1.2x the exchange's minimum notional;
2. ``mkt-sell``: MARKET SELL of what that buy actually received (fills minus
   any commission paid in BTC), rounded down to the step;
3. ``limit-far``: LIMIT BUY 5% under the bid, canceled as soon as it is open;
4. ``limit-near``: LIMIT BUY at the bid, canceled after 10 minutes if it has
   not filled (a partial fill is what this leg hopes to see);
5. ``near-sell``: MARKET SELL of whatever ``limit-near`` received, when that is
   above the minimum notional; a smaller remainder is left as dust.

Client order ids come from (book, cycle, leg), so after a restart the profile
finds its own orders in the store and carries on from the first leg that has
not ended; nothing is sent twice. A cycle that does not finish in its 4 hours
is left as it is (its open limit orders are canceled) and the next starts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent, client_order_id
from trading.execution.limits import PreTradeLimits
from trading.execution.orders import Order, OrderState
from trading.execution.rules import TradingRules
from trading.execution.types import OrderType, Side
from trading.ingest.quotes import Quote

BOOK = "tn-conformance-v1"
EVERY = timedelta(hours=4)
BUDGET = Decimal("200")                   # USDT this profile may reserve (§5.4 DEFAULT allocation)
NOTIONAL_MULT = Decimal("1.2")
FAR = Decimal("0.05")
NEAR_WAIT = timedelta(minutes=10)
CANCEL_RETRY = timedelta(seconds=30)
LEGS = ("mkt-buy", "mkt-sell", "limit-far", "limit-near", "near-sell")
# A 5% limit is the point of limit-far, so this book's price band is wider than the default 1%.
LIMITS = PreTradeLimits(max_order_notional=Decimal("50"), max_active_orders=2, max_orders_per_day=40, max_cancels_per_day=20,
                        max_price_deviation=Decimal("0.06"))
ZERO = Decimal(0)
OPEN = (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED)


@dataclass
class Step:
    intents: list[tuple[OrderIntent, Decimal]] = field(default_factory=list)   # (intent, unit price for the reservation)
    cancels: list[str] = field(default_factory=list)
    note: str = ""


def cycle_start(now: datetime) -> datetime:
    step = int(EVERY.total_seconds())
    return datetime.fromtimestamp(int(now.timestamp()) // step * step, UTC)


def cycle_id(now: datetime) -> str:
    return cycle_start(now).strftime("%Y%m%dT%H%MZ")


def net_base_qty(order: Order, base: str) -> Decimal | None:
    """Base asset an order actually received: fills minus commission paid in the base asset. None while fills are missing."""
    got = order.filled_qty_from_fills
    if got < order.filled_qty:
        return None
    return got - sum((f.fee for f in order.fills.values() if f.fee_asset == base), ZERO)


@dataclass
class Conformance:
    symbol: str = "BTCUSDT"
    base: str = "BTC"
    cancel_sent: dict[str, datetime] = field(default_factory=dict)
    cycles_done: int = 0
    last_cycle: str | None = None
    last_note: str = ""
    _done: set[str] = field(default_factory=set)

    def _intent(self, rules: TradingRules, cycle: str, leg: str, side: Side, qty: Decimal, price: Decimal | None, now: datetime,
                epoch: int) -> OrderIntent:
        return OrderIntent(mode=TradingMode.TESTNET, book=BOOK, decision_id=cycle, leg=leg, symbol=self.symbol, side=side,
                           order_type=OrderType.MARKET if price is None else OrderType.LIMIT, qty=qty, limit_price=price,
                           created_at=now, safety_epoch=epoch)

    def _make(self, leg: str, cycle: str, rules: TradingRules, q: Quote, now: datetime, epoch: int,
              lookup: Callable[[str], Order | None]) -> tuple[OrderIntent, Decimal] | str:
        """The intent for ``leg``, or a note saying why this leg is skipped or must wait."""
        target = rules.min_notional * NOTIONAL_MULT
        if leg == "mkt-buy":
            return self._intent(rules, cycle, leg, Side.BUY, rules.qty_for_notional(target, q.ask), None, now, epoch), q.ask
        if leg == "limit-far":
            price = rules.round_price(q.bid * (1 - FAR), Side.BUY)
            return self._intent(rules, cycle, leg, Side.BUY, rules.qty_for_notional(target, price), price, now, epoch), price
        if leg == "limit-near":
            price = rules.round_price(q.bid, Side.BUY)
            return self._intent(rules, cycle, leg, Side.BUY, rules.qty_for_notional(target, price), price, now, epoch), price
        source = lookup(client_order_id(BOOK, cycle, "mkt-buy" if leg == "mkt-sell" else "limit-near"))
        got = None if source is None else net_base_qty(source, self.base)
        if got is None:
            return "skip" if source is None else "wait: fills not loaded yet"
        qty = rules.round_qty(got)
        if qty < rules.min_qty or qty * q.bid < rules.min_notional:
            return "skip"
        return self._intent(rules, cycle, leg, Side.SELL, qty, None, now, epoch), q.bid

    def step(self, *, now: datetime, rules: TradingRules | None, quote: Quote | None, safety_epoch: int,
             lookup: Callable[[str], Order | None], open_orders: list[Order]) -> Step:
        """What to do now: at most one new intent, plus cancels. Called only while entries are allowed."""
        out = Step()
        cycle = cycle_id(now)
        for old in open_orders:          # limit orders left open by an earlier cycle
            if old.intent.book == BOOK and old.intent.decision_id != cycle and old.intent.order_type is OrderType.LIMIT and old.state in OPEN:
                self._cancel(old, now, out)
        if cycle in self._done:
            out.note = f"cycle {cycle} done"
            return out
        self.last_cycle = cycle
        for leg in LEGS:
            o = lookup(client_order_id(BOOK, cycle, leg))
            if o is None:
                if rules is None or quote is None:
                    out.note = f"{leg}: waiting for exchange rules and a fresh Testnet quote"
                    return self._noted(out)
                made = self._make(leg, cycle, rules, quote, now, safety_epoch, lookup)
                if made == "skip":
                    continue
                if isinstance(made, str):
                    out.note = f"{leg}: {made}"
                    return self._noted(out)
                out.intents.append(made)
                out.note = f"{leg}: sending"
                return self._noted(out)
            if o.is_terminal:
                continue
            if o.state in OPEN and (leg == "limit-far" or (leg == "limit-near" and now - o.intent.created_at >= NEAR_WAIT)):
                self._cancel(o, now, out)
            out.note = f"{leg}: {o.state.value.lower()}"
            return self._noted(out)
        self._done.add(cycle)
        self.cycles_done += 1
        out.note = f"cycle {cycle} done"
        return self._noted(out)

    def _cancel(self, o: Order, now: datetime, out: Step) -> None:
        sent = self.cancel_sent.get(o.client_order_id)
        if sent is None or now - sent >= CANCEL_RETRY:
            self.cancel_sent[o.client_order_id] = now
            out.cancels.append(o.client_order_id)

    def _noted(self, out: Step) -> Step:
        self.last_note = out.note
        return out

    def body(self) -> dict[str, object]:
        return {"book": BOOK, "cycle": self.last_cycle, "cycles_done": self.cycles_done, "note": self.last_note}
