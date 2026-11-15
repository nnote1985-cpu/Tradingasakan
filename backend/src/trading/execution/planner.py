"""Order planner: a target weight to the order legs that reach it (docs/m3-strategy-core-plan.md §3.2, A2).

One planner for Spot and USD-M Perp. The difference between the two is the sign of the position
and the ``reduce_only`` flag, not a second sizing path:

- **Spot** is long or flat. A negative target is refused here (``SPOT_NEGATIVE_WEIGHT``), not in the
  strategy. Buys add exposure; sells are ``reduce_only`` and never sell more than the book holds.
- **Perp** is signed (1x). A leg that moves the position toward zero is ``reduce_only``; a change of
  side is two legs: close all (``reduce_only``), then open the new side.

The Spot formulas are exactly those of ``R10aBot._execute`` in tn-r10a-code-v1.1, so a strategy moved
onto the planner sends the same orders (plan §4.2):

- equity = allocation + the book's cash change + held × bid;
- difference = weight × equity − held × bid;
- a buy is priced at the ask with the taker fee reserved, capped at 99.9% of the per-order cap, and
  rounded to 8 decimals then down to the lot step; a larger gap is closed by the next decisions;
- a sell is priced at the bid; a target of 0 sells everything held.

Nothing is sent when the rounded leg is below the exchange minimum (``BELOW_MIN``) or when the book's
cash cannot buy the minimum (``INSUFFICIENT_FUNDS``). The planner sizes only; the executor's pre-trade
checks, the budget reservation and Guardian still apply to every intent built from a leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from trading.contracts.strategy import MAX_ABS_WEIGHT, Market
from trading.execution.rules import TradingRules
from trading.execution.store import FEE_ALLOWANCE
from trading.execution.types import Side

ZERO = Decimal(0)
CAP_SHARE = Decimal("0.999")                # stay under the per-order notional cap after rounding
OFF_TARGET_SHARE = Decimal("0.1")           # a position more than 10% of equity from its target is re-traded
EIGHT = Decimal("1e-8")

# reason codes
SPOT_NEGATIVE_WEIGHT = "SPOT_NEGATIVE_WEIGHT"
WEIGHT_OUT_OF_RANGE = "WEIGHT_OUT_OF_RANGE"
BELOW_MIN = "BELOW_MIN"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
OPEN_LEG_BELOW_MIN = "OPEN_LEG_BELOW_MIN"   # perp side change: only the closing leg is sent
NO_QUOTE = "NO_QUOTE"


@dataclass(frozen=True)
class Leg:
    side: Side
    qty: Decimal
    price: Decimal                          # reference price: the ask for a buy, the bid for a sell
    reduce_only: bool

    @property
    def notional(self) -> Decimal:
        return self.qty * self.price


@dataclass(frozen=True)
class Plan:
    legs: tuple[Leg, ...]
    reason: str | None = None               # why fewer legs than needed were planned (None: on plan)
    refused: bool = False                   # the target itself is invalid for this market

    @property
    def empty(self) -> bool:
        return not self.legs


def spot_equity(allocation: Decimal, cash: Decimal, held: Decimal, bid: Decimal) -> Decimal:
    """The Spot book's equity as the Testnet bots value it: allocation + cash change + holdings at the bid."""
    return allocation + cash + held * bid


def off_target(weight: Decimal, held: Decimal, equity: Decimal, bid: Decimal, min_notional: Decimal) -> bool:
    """True when the position is far enough from ``weight`` to trade again although the target did not change."""
    return abs(weight * equity - held * bid) > max(min_notional, equity * OFF_TARGET_SHARE)


def _check_weight(market: Market, weight: Decimal) -> Plan | None:
    if not weight.is_finite() or abs(weight) > MAX_ABS_WEIGHT:
        return Plan((), WEIGHT_OUT_OF_RANGE, refused=True)
    if market is Market.SPOT and weight < 0:
        return Plan((), SPOT_NEGATIVE_WEIGHT, refused=True)
    return None


def _buy_qty(notional: Decimal, ask: Decimal, rules: TradingRules, fee: Decimal) -> Decimal:
    return rules.round_qty((notional / (ask * (1 + fee))).quantize(EIGHT, ROUND_DOWN))


def _valid(qty: Decimal, price: Decimal, rules: TradingRules) -> bool:
    return qty >= rules.min_qty and qty > 0 and qty * price >= rules.min_notional


def plan_spot(weight: Decimal, *, allocation: Decimal, cash: Decimal, held: Decimal, bid: Decimal, ask: Decimal,
              rules: TradingRules, max_order_notional: Decimal, fee: Decimal = FEE_ALLOWANCE,
              free_quote: Decimal | None = None) -> Plan:
    """The Spot leg that moves ``held`` toward ``weight`` of the book's equity (at most one leg).

    ``free_quote`` is the account's free quote balance when the caller has it; the buy is also capped by it.
    """
    bad = _check_weight(Market.SPOT, weight)
    if bad is not None:
        return bad
    if bid <= 0 or ask <= 0:
        return Plan((), NO_QUOTE)
    equity = spot_equity(allocation, cash, held, bid)
    delta = weight * equity - held * bid
    if delta > 0:
        book_cash = allocation + cash
        avail = book_cash if free_quote is None else min(book_cash, free_quote)
        want = min(delta, max_order_notional * CAP_SHARE)
        funded = min(want, avail)
        qty = _buy_qty(funded, ask, rules, fee) if funded > 0 else ZERO
        if not _valid(qty, ask, rules):
            # Cash only explains it when the gap itself was big enough to buy the minimum.
            short = funded < want and _valid(_buy_qty(want, ask, rules, fee), ask, rules)
            return Plan((), INSUFFICIENT_FUNDS if short else BELOW_MIN)
        return Plan((Leg(Side.BUY, qty, ask, reduce_only=False),))
    have = max(held, ZERO)
    qty = rules.round_qty(have if weight == 0 else min(have, -delta / bid))
    if not _valid(qty, bid, rules):
        return Plan((), BELOW_MIN)
    return Plan((Leg(Side.SELL, qty, bid, reduce_only=True),))


def plan_perp(weight: Decimal, *, equity: Decimal, position: Decimal, bid: Decimal, ask: Decimal, rules: TradingRules,
              max_order_notional: Decimal, fee: Decimal = FEE_ALLOWANCE, free_margin: Decimal | None = None) -> Plan:
    """The Perp legs (0-2) that move the signed ``position`` toward ``weight`` × ``equity`` at 1x.

    The position and the target are valued at the mid price. ``free_margin`` caps the opening notional
    (1x: margin = notional); it defaults to the equity not already in the position. Closing the old side
    frees its margin for the new side.
    """
    bad = _check_weight(Market.PERP, weight)
    if bad is not None:
        return bad
    if bid <= 0 or ask <= 0:
        return Plan((), NO_QUOTE)
    mid = (bid + ask) / 2
    target = weight * max(equity, ZERO) / mid              # signed base quantity wanted
    margin = (max(equity, ZERO) - abs(position) * mid) if free_margin is None else free_margin
    flips = position != 0 and target != 0 and (position > 0) != (target > 0)

    legs: list[Leg] = []
    if position != 0 and (flips or abs(target) < abs(position)):
        # Toward zero: reduce (or close everything when the target is flat or on the other side).
        close_all = flips or target == 0
        side = Side.SELL if position > 0 else Side.BUY
        price = bid if side is Side.SELL else ask
        qty = rules.round_qty(abs(position) if close_all else abs(position) - abs(target))
        if not _valid(qty, price, rules):
            return Plan((), BELOW_MIN)
        legs.append(Leg(side, qty, price, reduce_only=True))
        if not flips:
            return Plan(tuple(legs))
        margin += qty * mid
        opening = abs(target)
    elif abs(target) > abs(position):
        opening = abs(target) - abs(position)
    else:
        return Plan((), BELOW_MIN)                          # already at the target

    side = Side.BUY if target > 0 else Side.SELL
    price = ask if side is Side.BUY else bid
    want = min(opening * price, max_order_notional * CAP_SHARE)
    funded = min(want, max(margin, ZERO))
    qty = _buy_qty(funded, price, rules, fee) if funded > 0 else ZERO
    if not _valid(qty, price, rules):
        if legs:
            return Plan(tuple(legs), OPEN_LEG_BELOW_MIN)
        short = funded < want and _valid(_buy_qty(want, price, rules, fee), price, rules)
        return Plan((), INSUFFICIENT_FUNDS if short else BELOW_MIN)
    legs.append(Leg(side, qty, price, reduce_only=False))
    return Plan(tuple(legs))
