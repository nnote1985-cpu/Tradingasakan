"""Durable records for the Testnet worker (testnet-development-plan.md §7.2, §7.3, §8.6; P3).

The feeds thread never touches the database. It puts what it accepted on
``IngestService.records`` (closed bars, bar revisions, Testnet trades and
account checks), and the worker hands them to :class:`Recorder` on its own
schedule, on the same connection as the writer lease. A failed write keeps
the batch for the next flush; a lost database stops the worker through the
lease, and on restart :meth:`Recorder.sync_bars` re-sends any closed bars
the database has not seen from the local bar files.

Accounting rules kept here:

- an account epoch opens on the first conclusive account check, with the
  exchange's balances as the opening entry (not trading P&L);
- every trade is stored once per (account, epoch, symbol, trade id) and
  journalled per asset in balanced double entry; the fee is its own entry in
  its own asset, never converted;
- reconcile runs are stored when the verdict changes, on every discrepancy
  and every 15 minutes; the balance projection is replaced after each
  conclusive check.

Closing an epoch after a confirmed Testnet reset is an operator decision
(§8.6); :meth:`PersistStore.close_epoch` records it, nothing calls it on its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from trading.ingest.account import Balance, FillEvent
from trading.ingest.bars import Bar

VENUE = "BINANCE_SPOT_TESTNET"
SIGNAL_VENUE = "BINANCE_SPOT_MAINNET"            # signal-plane bars come from Mainnet public data (§3.3)
QUOTE_ASSETS = ("USDT", "FDUSD", "USDC", "TUSD", "BUSD", "BTC", "ETH", "BNB")
RECONCILE_EVERY_S = 15 * 60
BAR_BATCH = 500


@dataclass(frozen=True)
class ReconcileRecord:
    verdict: str
    at_ms: int
    first: bool
    differences: tuple[str, ...]
    balances: dict[str, Balance]
    open_orders: int


@dataclass(frozen=True)
class TradeRecord:
    fill: FillEvent
    received_ms: int


Record = tuple[str, Any]          # ("bar" | "revision", Bar), ("trade", TradeRecord), ("reconcile", ReconcileRecord)


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def split_symbol(symbol: str) -> tuple[str, str]:
    for q in QUOTE_ASSETS:
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)], q
    raise ValueError(f"cannot split {symbol} into base and quote")


def trade_lines(f: FillEvent) -> list[tuple[str, str, str, Decimal, str]]:
    """Journal lines (entry_key, asset, ledger_account, amount, kind) for one trade; each (entry, asset) sums to zero."""
    base, quote = split_symbol(f.symbol)
    sign = 1 if f.side == "BUY" else -1
    notional = f.qty * f.price
    key = f"trade:{f.symbol}:{f.trade_id}"
    lines = [(key, base, "WALLET", sign * f.qty, "TRADE"), (key, base, "MARKET", -sign * f.qty, "TRADE"),
             (key, quote, "WALLET", -sign * notional, "TRADE"), (key, quote, "MARKET", sign * notional, "TRADE")]
    if f.commission > 0 and f.commission_asset:
        fee = f"fee:{f.symbol}:{f.trade_id}"
        lines += [(fee, f.commission_asset, "WALLET", -f.commission, "FEE"), (fee, f.commission_asset, "FEES", f.commission, "FEE")]
    return lines


def opening_lines(epoch: int, balances: dict[str, Balance]) -> list[tuple[str, str, str, Decimal, str]]:
    key = f"opening:{epoch}"
    out = []
    for asset, b in sorted(balances.items()):
        total = b.free + b.locked
        if total != 0:
            out += [(key, asset, "WALLET", total, "OPENING"), (key, asset, "OPENING", -total, "OPENING")]
    return out


class PersistStore:
    """SQL for the P3 tables. Each method commits on its own; an autocommit connection is required."""

    def __init__(self, conn: psycopg.Connection[Any], account: str) -> None:
        if conn.autocommit is not True:
            raise ValueError("PersistStore manages its own transactions; pass an autocommit connection")
        self.conn, self.account = conn, account

    # --- bars -----------------------------------------------------------------------

    def last_bar_open(self, symbol: str, interval: str) -> int | None:
        row = self.conn.execute("select max(open_time) from trading.market_bars where venue = %s and symbol = %s and interval = %s",
                                (SIGNAL_VENUE, symbol, interval)).fetchone()
        return None if row is None or row[0] is None else int(row[0].timestamp() * 1000)

    def add_bars(self, bars: Iterable[Bar]) -> int:
        """Insert closed bars; an existing (venue, symbol, interval, open time) keeps its first value. Returns rows inserted."""
        rows = [(SIGNAL_VENUE, b.symbol, b.interval, _ts(b.open_time), _ts(b.close_time), b.open, b.high, b.low, b.close,
                 b.volume, b.source) for b in bars]
        added = 0
        with self.conn.transaction(), self.conn.cursor() as cur:
            for i in range(0, len(rows), BAR_BATCH):
                chunk = rows[i:i + BAR_BATCH]
                cur.execute("insert into trading.market_bars (venue, symbol, interval, open_time, close_time, open, high, low, close, "
                            "volume, source) select * from unnest(%s::text[], %s::text[], %s::text[], %s::timestamptz[], "
                            "%s::timestamptz[], %s::numeric[], %s::numeric[], %s::numeric[], %s::numeric[], %s::numeric[], %s::text[]) "
                            "on conflict do nothing", [list(c) for c in zip(*chunk, strict=True)])
                added += max(cur.rowcount, 0)
        return added

    def add_revision(self, b: Bar) -> bool:
        """Record a later, different value for a stored bar. False if the bar itself is not stored yet."""
        with self.conn.transaction():
            cur = self.conn.execute(
                "insert into trading.market_bar_revisions (venue, symbol, interval, open_time, open, high, low, close, volume, source) "
                "select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s where exists (select 1 from trading.market_bars "
                "where venue = %s and symbol = %s and interval = %s and open_time = %s)",
                (SIGNAL_VENUE, b.symbol, b.interval, _ts(b.open_time), b.open, b.high, b.low, b.close, b.volume, b.source,
                 SIGNAL_VENUE, b.symbol, b.interval, _ts(b.open_time)))
            return cur.rowcount == 1

    # --- epochs ---------------------------------------------------------------------

    def open_epoch(self) -> int | None:
        row = self.conn.execute("select epoch from trading.exec_account_epochs where account = %s and status = 'OPEN'",
                                (self.account,)).fetchone()
        return None if row is None else int(row[0])

    def start_epoch(self, balances: dict[str, Balance], at: datetime) -> int:
        """Open the next epoch with these balances as its opening entry. Refused while one is open."""
        with self.conn.transaction():
            self.conn.execute("select pg_advisory_xact_lock(hashtext('exec_epoch:' || %s))", (self.account,))
            if self.open_epoch() is not None:
                raise RuntimeError(f"account {self.account} already has an open epoch")
            row = self.conn.execute("select coalesce(max(epoch), 0) + 1 from trading.exec_account_epochs where account = %s",
                                    (self.account,)).fetchone()
            epoch = int(row[0]) if row else 1
            opening = {a: {"free": str(b.free), "locked": str(b.locked)} for a, b in sorted(balances.items()) if b.free + b.locked != 0}
            self.conn.execute("insert into trading.exec_account_epochs (account, epoch, venue, status, opened_at, opening_balances) "
                              "values (%s, %s, %s, 'OPEN', %s, %s)", (self.account, epoch, VENUE, at, json.dumps(opening)))
            self._journal(epoch, opening_lines(epoch, balances), at)
            return epoch

    def close_epoch(self, status: str, reason: str, at: datetime) -> None:
        """Close the open epoch, e.g. INTERRUPTED_TESTNET_RESET after a confirmed reset (§8.6). History stays."""
        if status not in ("CLOSED", "INTERRUPTED_TESTNET_RESET"):
            raise ValueError(status)
        self.conn.execute("update trading.exec_account_epochs set status = %s, closed_at = %s, close_reason = %s "
                          "where account = %s and status = 'OPEN'", (status, at, reason, self.account))

    # --- trades and journal ---------------------------------------------------------

    def _journal(self, epoch: int, lines: list[tuple[str, str, str, Decimal, str]], at: datetime) -> None:
        if lines:
            with self.conn.cursor() as cur:
                cur.executemany("insert into trading.exec_journal_lines values (%s, %s, %s, %s, %s, %s, %s, %s)",
                                [(self.account, epoch, k, asset, ledger, amount, kind, at) for k, asset, ledger, amount, kind in lines])

    def add_trade(self, epoch: int, t: TradeRecord) -> bool:
        """Store a trade and its journal lines once. False if this trade id is already stored for the epoch."""
        f = t.fill
        with self.conn.transaction():
            cur = self.conn.execute(
                "insert into trading.exec_trades values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                (self.account, epoch, f.symbol, f.trade_id, f.order_id, f.client_id, f.side, f.qty, f.price, f.commission,
                 f.commission_asset, _ts(f.trade_time), _ts(t.received_ms)))
            if cur.rowcount != 1:
                return False
            self._journal(epoch, trade_lines(f), _ts(f.trade_time))
            return True

    def wallet(self, epoch: int) -> dict[str, Decimal]:
        """Per-asset wallet balance the journal implies (opening + trades - fees)."""
        return {a: Decimal(v) for a, v in self.conn.execute(
            "select asset, sum(amount) from trading.exec_journal_lines where account = %s and epoch = %s and ledger_account = 'WALLET' "
            "group by asset", (self.account, epoch))}

    # --- account checks -------------------------------------------------------------

    def add_reconciliation(self, epoch: int | None, r: ReconcileRecord) -> None:
        self.conn.execute("insert into trading.exec_reconciliations (account, epoch, checked_at, verdict, first_check, differences, "
                          "open_orders) values (%s, %s, %s, %s, %s, %s, %s)",
                          (self.account, epoch, _ts(r.at_ms), r.verdict, r.first, json.dumps(list(r.differences)), r.open_orders))

    def replace_balances(self, epoch: int, balances: dict[str, Balance], at: datetime) -> None:
        with self.conn.transaction():
            self.conn.execute("delete from trading.exec_balances where account = %s and epoch = %s", (self.account, epoch))
            rows = [(self.account, epoch, a, b.free, b.locked, at) for a, b in sorted(balances.items()) if b.free + b.locked != 0]
            if rows:
                with self.conn.cursor() as cur:
                    cur.executemany("insert into trading.exec_balances values (%s, %s, %s, %s, %s, %s)", rows)

    # --- heartbeat ------------------------------------------------------------------

    def heartbeat(self, owner: str, host: str, started_at: str, last_tick_at: str, body: dict[str, Any]) -> None:
        self.conn.execute("insert into trading.worker_heartbeats (account, owner, host, started_at, last_tick_at, body) "
                          "values (%s, %s, %s, %s, %s, %s) on conflict (account) do update set owner = excluded.owner, "
                          "host = excluded.host, started_at = excluded.started_at, last_tick_at = excluded.last_tick_at, "
                          "body = excluded.body, updated_at = now()",
                          (self.account, owner, host, started_at, last_tick_at, json.dumps(body, default=str)))


@dataclass
class Recorder:
    """Turns feed records into store writes; keeps whatever could not be written for the next flush."""

    store: PersistStore
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    pending: list[Record] = field(default_factory=list)
    epoch: int | None = None
    last_verdict: str | None = None
    last_stored_ms: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {"bars": 0, "revisions": 0, "trades": 0, "reconciliations": 0})

    def sync_bars(self, symbol: str, interval: str, closed: list[Bar]) -> int:
        """Send local closed bars newer than the newest stored one (all of them on an empty table)."""
        last = self.store.last_bar_open(symbol, interval)
        n = self.store.add_bars([b for b in closed if last is None or b.open_time > last])
        self.counts["bars"] += n
        return n

    def flush(self, records: Iterable[Record]) -> None:
        """Write pending and new records in order. Raises on a database error; unwritten records stay pending."""
        self.pending.extend(records)
        if self.epoch is None:
            self.epoch = self.store.open_epoch()
        bars = [r[1] for r in self.pending if r[0] == "bar"]
        if bars:
            self.counts["bars"] += self.store.add_bars(bars)
            self.pending = [r for r in self.pending if r[0] != "bar"]
        waiting: list[Record] = []
        try:
            while self.pending:
                kind, item = self.pending[0]
                if kind == "revision":
                    if self.store.add_revision(item):
                        self.counts["revisions"] += 1
                elif kind == "trade":
                    if self.epoch is None:
                        waiting.append(self.pending[0])          # no epoch until the first conclusive account check
                    elif self.store.add_trade(self.epoch, item):
                        self.counts["trades"] += 1
                elif kind == "reconcile":
                    self._reconcile(item)
                self.pending.pop(0)
                if not self.pending and waiting and self.epoch is not None:
                    self.pending, waiting = waiting, []      # the epoch opened later in this batch
        finally:
            self.pending = waiting + self.pending

    def _reconcile(self, r: ReconcileRecord) -> None:
        conclusive = r.verdict != "INCONCLUSIVE"
        at = _ts(r.at_ms)
        if conclusive and self.epoch is None:
            self.epoch = self.store.start_epoch(r.balances, at)
        if r.verdict != self.last_verdict or r.verdict == "DISCREPANCY" or r.at_ms - self.last_stored_ms >= RECONCILE_EVERY_S * 1000:
            self.store.add_reconciliation(self.epoch, r)
            self.counts["reconciliations"] += 1
            self.last_stored_ms = r.at_ms
        self.last_verdict = r.verdict
        if conclusive and self.epoch is not None:
            self.store.replace_balances(self.epoch, r.balances, at)
