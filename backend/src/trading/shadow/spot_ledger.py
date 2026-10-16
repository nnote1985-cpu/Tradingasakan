"""Shadow ledger for a spot pair (BUILDPLAN v4.2 §11; docs/plan-spot-and-options.md).

Same append-only, duplicate-safe Decimal journal as :class:`ShadowLedger`, with
spot accounting: a buy pays the full cost from USDT cash and puts the base asset
into ``inventory_at_cost``; a sell takes inventory out at average cost and books
the difference as ``realized_pnl``. There are no short sales, no leverage and no
funding. Buys cannot spend more cash than there is, and sells cannot exceed the
quantity held. Fees are charged in the quote asset (USDT).
"""

from __future__ import annotations

from decimal import Decimal

from trading.shadow.ledger import ZERO, D, ShadowLedger


class InsufficientBalance(ValueError):
    pass


class SpotLedger(ShadowLedger):
    instrument_type = "SPOT"

    def fill(self, exec_id: str, at, side: str, qty, price, fee, reference_price=None) -> bool:
        if exec_id in self._keys:
            return False
        q, p, f = D(qty), D(price), D(fee)
        if q <= 0 or p <= 0 or f < 0 or side not in ("BUY", "SELL"):
            raise ValueError("invalid fill")
        pos = self.position
        slip = None
        if reference_price is not None:
            r = D(reference_price)
            slip = (p - r) * q if side == "BUY" else (r - p) * q
        if side == "BUY":
            cost = p * q
            if cost + f > self.bal("cash"):
                raise InsufficientBalance(f"buy needs {cost + f} USDT, cash is {self.bal('cash')}")
            lines = [("cash", -(cost + f)), ("inventory_at_cost", cost), ("fees", f)]
            realized = ZERO
        else:
            if q > pos.qty:
                raise InsufficientBalance(f"sell {q} but only {pos.qty} held (no short sales on spot)")
            at_cost = pos.avg_entry * q
            proceeds = p * q
            realized = proceeds - at_cost
            lines = [("cash", proceeds - f), ("inventory_at_cost", -at_cost), ("realized_pnl", -realized), ("fees", f)]
        self._post(exec_id, at, "fill", lines,
                   {"side": side, "qty": str(q), "price": str(p), "fee": str(f), "realized": str(realized),
                    "slippage": None if slip is None else str(slip)})
        if side == "BUY":
            pos.avg_entry = (pos.avg_entry * pos.qty + p * q) / (pos.qty + q)
            pos.qty += q
        else:
            pos.qty -= q
            if pos.qty == 0:
                pos.avg_entry = ZERO
        return True

    def funding(self, funding_id: str, at, rate, mark) -> bool:
        return False                                      # spot holdings pay no funding

    def unrealized(self, mark) -> Decimal:
        pos = self.position
        return ZERO if pos.qty == 0 else (D(mark) - pos.avg_entry) * pos.qty

    def trading_equity(self, mark) -> Decimal:
        """USDT cash plus the base asset valued at ``mark``."""
        return self.bal("cash") + self.position.qty * D(mark)

    def check(self) -> list[str]:
        problems = super().check()
        if self.position.qty < 0:
            problems.append("negative base quantity on spot")
        if self.bal("cash") < 0:
            problems.append("negative cash on spot")
        if self.bal("inventory_at_cost") != self.position.avg_entry * self.position.qty:
            # Rounding in the average can leave a tiny residue; anything material is a bug.
            if abs(self.bal("inventory_at_cost") - self.position.avg_entry * self.position.qty) > Decimal("1e-9"):
                problems.append("inventory at cost differs from position x average cost")
        return problems

    def to_dict(self) -> dict:
        return {**super().to_dict(), "instrument_type": self.instrument_type}
