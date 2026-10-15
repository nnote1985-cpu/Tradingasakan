"""Shadow ledger for one USDT-settled linear perpetual (BUILDPLAN v4.2 §11).

Append-only, Decimal, double entry. Every economic event is one journal entry
whose lines sum to zero, keyed by the event's source key: posting the same key
again changes nothing (§20.2, T16). Position, average entry, cash and costs are
projections of the journal, never edited directly.

Accounts (USDT): ``cash`` is settlement cash; ``capital`` is the opening
balance; ``realized_pnl`` (credit = profit), ``fees``, ``funding`` (debit =
paid) and ``operating_costs``. Operating costs (LLM, VPS, data) do not leave the
exchange wallet: they are booked against ``operating_costs_payable``, so they
reduce economic equity but not trading equity (§11.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

ZERO = Decimal(0)


def D(x) -> Decimal:
    v = x if isinstance(x, Decimal) else Decimal(str(x))
    if not v.is_finite():
        raise ValueError(f"non-finite amount {x!r}")
    return v


@dataclass(frozen=True)
class Entry:
    key: str
    at: str
    kind: str
    lines: tuple[tuple[str, Decimal], ...]
    data: dict = field(default_factory=dict)


@dataclass
class Position:
    qty: Decimal = ZERO          # signed: + long, - short
    avg_entry: Decimal = ZERO    # weighted by filled quantity, fees excluded


class ShadowLedger:
    def __init__(self, currency: str = "USDT"):
        self.currency = currency
        self.entries: list[Entry] = []
        self._keys: set[str] = set()
        self.position = Position()
        self.balances: dict[str, Decimal] = {}

    # ---- posting ---------------------------------------------------------
    def _post(self, key: str, at: datetime | str, kind: str, lines: list[tuple[str, Decimal]], data: dict) -> bool:
        if key in self._keys:
            return False
        lines = [(a, v) for a, v in lines if v != 0]
        if sum((v for _, v in lines), ZERO) != 0:
            raise ValueError(f"unbalanced entry {key}: {lines}")
        stamp = at if isinstance(at, str) else at.isoformat()
        self.entries.append(Entry(key, stamp, kind, tuple(lines), data))
        self._keys.add(key)
        for a, v in lines:
            self.balances[a] = self.balances.get(a, ZERO) + v
        return True

    def open(self, key: str, at, amount) -> bool:
        a = D(amount)
        return self._post(key, at, "open", [("cash", a), ("capital", -a)], {"amount": str(a)})

    def fill(self, exec_id: str, at, side: str, qty, price, fee, reference_price=None) -> bool:
        """One execution. ``side`` BUY or SELL; ``fee`` in USDT (>= 0).
        Returns False when ``exec_id`` was already posted."""
        if exec_id in self._keys:
            return False
        q, p, f = D(qty), D(price), D(fee)
        if q <= 0 or p <= 0 or f < 0 or side not in ("BUY", "SELL"):
            raise ValueError("invalid fill")
        signed = q if side == "BUY" else -q
        pos = self.position
        realized = ZERO
        closing = ZERO
        if pos.qty != 0 and (pos.qty > 0) != (signed > 0):
            closing = min(abs(signed), abs(pos.qty))
            direction = 1 if pos.qty > 0 else -1
            realized = (p - pos.avg_entry) * closing * direction
        opening = abs(signed) - closing
        lines = [("cash", realized - f), ("realized_pnl", -realized), ("fees", f)]
        slip = None
        if reference_price is not None:
            r = D(reference_price)
            slip = (p - r) * q if side == "BUY" else (r - p) * q   # cost vs reference, a metric only
        posted = self._post(exec_id, at, "fill", lines,
                            {"side": side, "qty": str(q), "price": str(p), "fee": str(f),
                             "realized": str(realized), "slippage": None if slip is None else str(slip)})
        if not posted:
            return False
        new_qty = pos.qty + signed
        if opening > 0:
            prev = abs(pos.qty) if closing == 0 else ZERO
            base = pos.avg_entry if closing == 0 else ZERO
            pos.avg_entry = (base * prev + p * opening) / (prev + opening)
        if new_qty == 0:
            pos.avg_entry = ZERO
        pos.qty = new_qty
        return True

    def funding(self, funding_id: str, at, rate, mark) -> bool:
        """Funding on the position held at the print: longs pay positive rates."""
        if self.position.qty == 0 or funding_id in self._keys:
            return False
        pay = self.position.qty * D(mark) * D(rate)     # > 0 means paid
        return self._post(funding_id, at, "funding", [("cash", -pay), ("funding", pay)],
                          {"rate": str(D(rate)), "mark": str(D(mark)), "qty": str(self.position.qty), "paid": str(pay)})

    def operating_cost(self, cost_id: str, at, amount, memo: str = "") -> bool:
        a = D(amount)
        if a < 0:
            raise ValueError("operating cost must be >= 0")
        return self._post(cost_id, at, "operating_cost",
                          [("operating_costs", a), ("operating_costs_payable", -a)], {"amount": str(a), "memo": memo})

    # ---- projections -------------------------------------------------------
    def bal(self, account: str) -> Decimal:
        return self.balances.get(account, ZERO)

    def unrealized(self, mark) -> Decimal:
        pos = self.position
        return ZERO if pos.qty == 0 else (D(mark) - pos.avg_entry) * pos.qty

    def trading_equity(self, mark) -> Decimal:
        return self.bal("cash") + self.unrealized(mark)

    def economic_equity(self, mark) -> Decimal:
        return self.trading_equity(mark) - self.bal("operating_costs")

    def check(self) -> list[str]:
        """Invariants: every entry balances; balances equal the sum of entries."""
        problems, sums = [], {}
        for e in self.entries:
            if sum((v for _, v in e.lines), ZERO) != 0:
                problems.append(f"{e.key} unbalanced")
            for a, v in e.lines:
                sums[a] = sums.get(a, ZERO) + v
        if {a: v for a, v in sums.items() if v != 0} != {a: v for a, v in self.balances.items() if v != 0}:
            problems.append("balances differ from journal")
        return problems

    # ---- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"currency": self.currency,
                "entries": [{"key": e.key, "at": e.at, "kind": e.kind,
                             "lines": [[a, str(v)] for a, v in e.lines], "data": e.data} for e in self.entries],
                "position": {"qty": str(self.position.qty), "avg_entry": str(self.position.avg_entry)}}

    @classmethod
    def from_dict(cls, d: dict) -> ShadowLedger:
        led = cls(d["currency"])
        for e in d["entries"]:
            led._post(e["key"], e["at"], e["kind"], [(a, Decimal(v)) for a, v in e["lines"]], e["data"])
        led.position = Position(Decimal(d["position"]["qty"]), Decimal(d["position"]["avg_entry"]))
        return led
