"""Immutable order intents with a stable client order id (§12.2, §12.3).

The client order id is derived from the business key (book, decision, leg), so
retrying the same decision always reuses the same id. A new id is never minted
to get around an UNKNOWN outcome; a genuinely new attempt needs a new leg.
Binance accepts up to 36 characters from ``[A-Za-z0-9._-]``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading.contracts.enums import TradingMode
from trading.execution.rules import TradingRules
from trading.execution.types import OrderType, Side

CLIENT_ID_PREFIX = "tak-"


def client_order_id(book: str, decision_id: str, leg: str) -> str:
    key = "\x1f".join((book, decision_id, leg)).encode()
    return CLIENT_ID_PREFIX + hashlib.sha256(key).hexdigest()[:32]


@dataclass(frozen=True)
class OrderIntent:
    mode: TradingMode
    book: str
    decision_id: str
    leg: str
    symbol: str
    side: Side
    order_type: OrderType
    qty: Decimal
    limit_price: Decimal | None
    created_at: datetime
    safety_epoch: int
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if self.mode is not TradingMode.TESTNET:
            raise ValueError(f"order intents are TESTNET only in this build, not {self.mode}")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("limit_price is required for LIMIT orders only")
        if not all((self.book, self.decision_id, self.leg, self.symbol)):
            raise ValueError("book, decision_id, leg and symbol are required")
        if self.reduce_only and self.side is Side.BUY:
            raise ValueError("a spot buy always adds exposure; reduce_only is for sells only")

    @property
    def client_order_id(self) -> str:
        return client_order_id(self.book, self.decision_id, self.leg)

    @property
    def increases_exposure(self) -> bool:
        """Spot long/flat: every buy adds exposure, a sell only reduces it. ``reduce_only`` never exempts a buy."""
        return self.side is Side.BUY

    @classmethod
    def build(cls, rules: TradingRules, *, side: Side, qty: Decimal, limit_price: Decimal | None = None,
              **kw: object) -> OrderIntent:
        """Build an intent with quantity and price already rounded through ``rules``."""
        q = rules.round_qty(qty)
        p = None if limit_price is None else rules.round_price(limit_price, side)
        order_type = OrderType.MARKET if p is None else OrderType.LIMIT
        return cls(side=side, qty=q, limit_price=p, order_type=order_type, symbol=rules.symbol, **kw)  # type: ignore[arg-type]
