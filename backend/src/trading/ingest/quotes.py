"""Latest executable bid/ask from the Testnet bookTicker stream (testnet-development-plan.md §4.2).

The bookTicker event carries an update id but no event time, so freshness is
measured from when the message arrived here. An update id at or below the
one held is out of order and is ignored. An entry needs a quote no older than
5 s (limits.py), and the feed counts as silent after 10 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ENTRY_MAX_AGE_MS = 5_000
SILENT_AFTER_MS = 10_000


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    bid_qty: Decimal
    ask: Decimal
    ask_qty: Decimal
    update_id: int
    received_ms: int


def quote_from_stream(msg: dict[str, Any], received_ms: int) -> Quote | None:
    if not {"u", "s", "b", "B", "a", "A"} <= msg.keys():
        return None
    return Quote(str(msg["s"]), Decimal(msg["b"]), Decimal(msg["B"]), Decimal(msg["a"]), Decimal(msg["A"]), int(msg["u"]), received_ms)


class QuoteBook:
    def __init__(self) -> None:
        self.latest: dict[str, Quote] = {}
        self.out_of_order = 0

    def accept(self, q: Quote) -> bool:
        held = self.latest.get(q.symbol)
        if held is not None and q.update_id <= held.update_id:
            self.out_of_order += 1
            return False
        if q.bid <= 0 or q.ask <= 0 or q.bid > q.ask:
            return False
        self.latest[q.symbol] = q
        return True

    def age_ms(self, symbol: str, now_ms: int) -> int | None:
        q = self.latest.get(symbol)
        return None if q is None else now_ms - q.received_ms

    def fresh_for_entry(self, symbol: str, now_ms: int) -> Quote | None:
        age = self.age_ms(symbol, now_ms)
        return self.latest[symbol] if age is not None and age <= ENTRY_MAX_AGE_MS else None
