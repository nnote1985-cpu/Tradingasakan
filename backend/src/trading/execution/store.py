"""Durable execution store in the private ``trading`` schema (migration ``*_execution_store.sql``).

Note chose Supabase for this store on 2026-10-16. Tables, all scoped by an
account name:

- ``exec_intents``: immutable order intents, unique by client order id and by
  (book, decision, leg); TESTNET only at the database level too;
- ``exec_reservations``: risk reserved for open buys, account-wide;
- ``exec_outbox``: EXECUTE_INTENT events the gateway claims under its lease;
- ``exec_order_events`` / ``exec_fills``: append-only order history and fills
  (one row per execution id, so a repeated fill is ignored);
- ``exec_safety``: safety state, epoch and HALT latch;
- ``exec_writer_lease``: one writer per account, with a fencing token that
  grows on every new owner (§14.3).

Accepting an intent, reserving its risk and queueing the outbox event commit
in one transaction (§12.2). Gateway writes carry the fencing token and are
refused when it is no longer the current, unexpired lease. When the database
cannot be reached, Guardian writes to :mod:`trading.execution.journal` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from trading.contracts.enums import TradingMode
from trading.execution.intent import OrderIntent
from trading.execution.orders import TERMINAL, Fill, Order, OrderState
from trading.execution.safety import AccountSafety, SafetyState
from trading.execution.types import OrderType, Side

ZERO = Decimal(0)


class StaleFencingToken(RuntimeError):
    pass


class IntentRefused(ValueError):
    pass


@dataclass(frozen=True)
class Lease:
    owner: str
    token: int
    expires_at: datetime


def _intent(row: tuple[Any, ...]) -> OrderIntent:
    mode, book, decision_id, leg, symbol, side, otype, qty, limit_price, reduce_only, epoch, created_at = row
    return OrderIntent(mode=TradingMode(mode), book=book, decision_id=decision_id, leg=leg, symbol=symbol, side=Side(side),
                       order_type=OrderType(otype), qty=qty, limit_price=limit_price, created_at=created_at,
                       safety_epoch=epoch, reduce_only=reduce_only)


_INTENT_COLS = "mode, book, decision_id, leg, symbol, side, order_type, qty, limit_price, reduce_only, safety_epoch, created_at"


def _norm(d: Decimal | None) -> Decimal | None:
    """NUMERIC(38,18) comes back with 18 places; normalise so it compares and prints like the intent's own value."""
    if d is None:
        return None
    n = d.normalize()
    return n.quantize(Decimal(1)) if n == n.to_integral_value() else n


class ExecStore:
    def __init__(self, conn: psycopg.Connection[Any], account: str) -> None:
        if conn.autocommit is not True:
            raise ValueError("ExecStore manages its own transactions; pass an autocommit connection")
        self.conn = conn
        self.account = account

    # --- writer lease and fencing (§14.3) ---------------------------------

    def acquire_lease(self, owner: str, now: datetime, ttl: timedelta) -> Lease | None:
        """Take or renew the writer lease. A new owner gets a larger token; None while another owner holds it."""
        with self.conn.transaction():
            row = self.conn.execute("select owner, token, expires_at from trading.exec_writer_lease where account = %s for update",
                                    (self.account,)).fetchone()
            if row is None:
                token = 1
            elif row[0] == owner:
                token = row[1]
            elif row[2] > now:
                return None
            else:
                token = row[1] + 1
            expires = now + ttl
            self.conn.execute("insert into trading.exec_writer_lease values (%s, %s, %s, %s) on conflict (account) do update set "
                              "owner = excluded.owner, token = excluded.token, expires_at = excluded.expires_at",
                              (self.account, owner, token, expires))
            return Lease(owner, token, expires)

    def _check_token(self, token: int, now: datetime) -> None:
        row = self.conn.execute("select token, expires_at from trading.exec_writer_lease where account = %s for share",
                                (self.account,)).fetchone()
        if row is None or row[0] != token or row[1] <= now:
            raise StaleFencingToken(f"token {token} is not the current unexpired lease")

    # --- safety -----------------------------------------------------------

    def load_safety(self) -> AccountSafety:
        """Stored safety, reopened as RECOVERING after a restart (§15.1); the HALT latch and epoch survive."""
        row = self.conn.execute("select state, epoch, halt_reason from trading.exec_safety where account = %s",
                                (self.account,)).fetchone()
        if row is None:
            return AccountSafety()
        state, epoch, reason = row
        if state in (SafetyState.HALTING, SafetyState.HALTED):
            return AccountSafety(state=SafetyState(state), epoch=epoch, halt_reason=reason)
        return AccountSafety(state=SafetyState.RECOVERING, epoch=epoch, halt_reason=reason)

    def save_safety(self, s: AccountSafety) -> None:
        self.conn.execute(
            "insert into trading.exec_safety (account, state, epoch, halt_reason, reducing_until) values (%s, %s, %s, %s, %s) "
            "on conflict (account) do update set state = excluded.state, epoch = excluded.epoch, halt_reason = excluded.halt_reason, "
            "reducing_until = excluded.reducing_until, updated_at = now()",
            (self.account, s.state.value, s.epoch, s.halt_reason, s.reducing_until))

    # --- intents, reservations, outbox (§12.2) ----------------------------

    def reserved(self) -> Decimal:
        row = self.conn.execute("select coalesce(sum(r.qty * r.unit_price), 0) from trading.exec_reservations r "
                                "join trading.exec_intents i using (client_order_id) where i.account = %s", (self.account,)).fetchone()
        return Decimal(row[0]) if row else ZERO

    def accept_intent(self, intent: OrderIntent, *, unit_price: Decimal, budget: Decimal) -> bool:
        """Insert the intent, reserve a buy's notional and queue EXECUTE_INTENT in one transaction.

        Returns False if this client order id was already accepted (nothing changes). Raises
        :class:`IntentRefused` on a stale epoch, blocked entries or too little budget. The safety
        row is locked for the transaction, so two acceptances cannot both spend the same budget.
        """
        cid = intent.client_order_id
        with self.conn.transaction():
            row = self.conn.execute("select state, epoch from trading.exec_safety where account = %s for update",
                                    (self.account,)).fetchone()
            if self.conn.execute("select 1 from trading.exec_intents where client_order_id = %s", (cid,)).fetchone():
                return False
            state, epoch = (SafetyState.RECOVERING.value, 1) if row is None else row
            if intent.safety_epoch != epoch:
                raise IntentRefused("STALE_SAFETY_EPOCH")
            if intent.increases_exposure:
                if state != SafetyState.RUNNING:
                    raise IntentRefused(f"ENTRIES_BLOCKED_{state}")
                if self.reserved() + intent.qty * unit_price > budget:
                    raise IntentRefused("INSUFFICIENT_BUDGET")
            self.conn.execute(
                f"insert into trading.exec_intents (client_order_id, account, {_INTENT_COLS}) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (cid, self.account, intent.mode.value, intent.book, intent.decision_id, intent.leg, intent.symbol, intent.side.value,
                 intent.order_type.value, intent.qty, intent.limit_price, intent.reduce_only, intent.safety_epoch, intent.created_at))
            if intent.increases_exposure:
                self.conn.execute("insert into trading.exec_reservations values (%s, %s, %s)", (cid, intent.qty, unit_price))
            self.conn.execute("insert into trading.exec_outbox (client_order_id, kind, status) values (%s, 'EXECUTE_INTENT', 'PENDING')",
                              (cid,))
            self.conn.execute("insert into trading.exec_order_events values (%s, 0, 'INTENT', 0, null, 'accepted', 0, %s)",
                              (cid, intent.created_at))
            return True

    def claim_outbox(self, token: int, now: datetime) -> list[tuple[int, OrderIntent]]:
        """Claim this account's pending EXECUTE_INTENT rows.

        A row claimed by an older token (a writer that crashed) is never handed out again: its
        order may have reached the exchange, so it is reconciled from ``unresolved_orders``.
        """
        with self.conn.transaction():
            self._check_token(token, now)
            rows = self.conn.execute(
                f"select o.id, {', '.join('i.' + c.strip() for c in _INTENT_COLS.split(','))} from trading.exec_outbox o "
                "join trading.exec_intents i using (client_order_id) where o.status = 'PENDING' and i.account = %s "
                "order by o.id for update of o skip locked", (self.account,)).fetchall()
            for r in rows:
                self.conn.execute("update trading.exec_outbox set status = 'CLAIMED', claimed_by_token = %s where id = %s", (token, r[0]))
            return [(r[0], _intent(r[1:])) for r in rows]

    def finish_outbox(self, outbox_id: int, token: int, now: datetime) -> None:
        with self.conn.transaction():
            self._check_token(token, now)
            self.conn.execute("update trading.exec_outbox set status = 'DONE' where id = %s", (outbox_id,))

    # --- orders -----------------------------------------------------------

    def record_order(self, order: Order, token: int, now: datetime, reason: str = "") -> None:
        """Persist the order's state, new fills and remaining reservation under the fencing token."""
        cid = order.client_order_id
        with self.conn.transaction():
            self._check_token(token, now)
            last = self.conn.execute("select seq, state, cum_qty, exchange_order_id from trading.exec_order_events "
                                     "where client_order_id = %s order by seq desc limit 1", (cid,)).fetchone()
            if last is None:
                raise KeyError(f"unknown intent {cid}")
            if (last[1], last[2], last[3]) != (order.state.value, order.reported_cum_qty, order.exchange_order_id):
                self.conn.execute("insert into trading.exec_order_events values (%s, %s, %s, %s, %s, %s, %s, %s)",
                                  (cid, last[0] + 1, order.state.value, order.reported_cum_qty, order.exchange_order_id,
                                   reason, token, now))
            for f in order.fills.values():
                self.conn.execute("insert into trading.exec_fills values (%s, %s, %s, %s, %s, %s, %s) on conflict (exec_id) do nothing",
                                  (f.exec_id, cid, f.qty, f.price, f.fee, f.fee_asset, f.at))
            if order.is_terminal:
                self.conn.execute("delete from trading.exec_reservations where client_order_id = %s", (cid,))
            else:
                self.conn.execute("update trading.exec_reservations set qty = %s where client_order_id = %s",
                                  (order.intent.qty - order.filled_qty, cid))

    def load_order(self, client_order_id: str) -> Order:
        row = self.conn.execute(f"select {_INTENT_COLS} from trading.exec_intents where client_order_id = %s and account = %s",
                                (client_order_id, self.account)).fetchone()
        if row is None:
            raise KeyError(client_order_id)
        r = list(row)
        r[7], r[8] = _norm(r[7]), _norm(r[8])
        order = Order(_intent(tuple(r)))
        for exec_id, qty, price, fee, asset, at in self.conn.execute(
                "select exec_id, qty, price, fee, fee_asset, at from trading.exec_fills where client_order_id = %s order by at, exec_id",
                (client_order_id,)):
            order.fills[exec_id] = Fill(exec_id, _norm(qty) or ZERO, _norm(price) or ZERO, _norm(fee) or ZERO, asset, at)
        for state, cum, eid, reason in self.conn.execute(
                "select state, cum_qty, exchange_order_id, reason from trading.exec_order_events where client_order_id = %s order by seq",
                (client_order_id,)):
            order.state = OrderState(state)
            order.reported_cum_qty = _norm(cum) or ZERO
            order.exchange_order_id = eid
            order.history.append((order.state, reason))
        return order

    def unresolved_orders(self) -> list[Order]:
        """Every order not known to be terminal: what RECOVERING must reconcile before RUNNING."""
        ids = [r[0] for r in self.conn.execute(
            "select client_order_id from trading.exec_intents where account = %s order by created_at, client_order_id", (self.account,))]
        return [o for o in (self.load_order(i) for i in ids) if o.state not in TERMINAL]
