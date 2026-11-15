"""A bot whose settings come from an approved revision in ``trading.bots`` (m4-bot-config-plan.md §3.5).

``ConfiguredBot`` wraps the M3 :class:`~trading.runtime.target_bot.TargetBot` and adds what a bot
configured from the web needs:

- it runs the revision the worker loaded and verified at start (``trading.bots.loader``);
- every 5 minutes it looks for a newer approved revision or a retire. A newer revision is used only
  when its hash checks out and the book is flat with no order open; until then the heartbeat shows it
  as pending. Account, allocation and strategy never change between revisions (the API refuses it),
  so a switch changes only the name, notes and SHADOW/TESTNET;
- a retired bot stops deciding and trading;
- ``identity`` is what the heartbeat reports as ``execution.bot`` (``bot_id@revision`` and hash).

It has the ``step``/``body``/``close`` interface of ``R10aBot``, so the executor runs it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trading.bots import loader, templates
from trading.bots.loader import ApprovedRevision
from trading.runtime.alerts import Severity
from trading.runtime.target_bot import TargetBot

if TYPE_CHECKING:
    from trading.execution.store import ExecStore
    from trading.runtime.executor import Executor

CHECK_EVERY_S = 300.0


def target_bot(store: ExecStore, rev: ApprovedRevision, **kw: Any) -> TargetBot:
    c = rev.config
    t = templates.get(c.strategy.template)
    if t is None:
        raise loader.BotLoadRefused("unknown_template", f"ไม่มีกลยุทธ์ {c.strategy.template} ใน worker รุ่นนี้")
    return TargetBot(store, t.factory(t.resolve(c.strategy.params)), book=rev.book, allocation=Decimal(c.allocation.amount),
                     label=c.name, tag=f"b{rev.bot_id.hex[:8]}", send_orders=c.mode == "TESTNET", **kw)


@dataclass
class ConfiguredBot:
    store: ExecStore
    rev: ApprovedRevision
    inner: TargetBot
    pending: ApprovedRevision | None = None
    retired: bool = False
    note: str = ""
    _next_check: float = 0.0
    _seen_refusal: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, store: ExecStore, rev: ApprovedRevision, **kw: Any) -> ConfiguredBot:
        return cls(store, rev, target_bot(store, rev, **kw))

    @property
    def book(self) -> str:
        return self.inner.book

    @property
    def allocation(self) -> Decimal:
        return self.inner.allocation

    @property
    def rules_version(self) -> str:
        return self.inner.rules_version

    @property
    def identity(self) -> dict[str, Any]:
        return {**self.rev.identity(), "pending_revision": None if self.pending is None else self.pending.revision,
                "retired": self.retired, "note": self.note or None}

    def step(self, ex: Executor, token: int) -> None:
        if ex.mono() >= self._next_check:
            self._next_check = ex.mono() + CHECK_EVERY_S
            self._check(ex)
        if self.pending is not None:
            self._try_switch(ex)
        if not self.retired:
            self.inner.step(ex, token)

    def _check(self, ex: Executor) -> None:
        newest, retired, refusal = loader.newest(self.store.conn, self.rev.bot_id, self.store.account)
        if retired and not self.retired:
            self.retired, self.note = True, "retired: no more decisions or orders"
            ex.alerts.push(Severity.WARNING, f"bot-{self.inner.tag}-retired", f"บอท {self.rev.config.name} ถูกปลดแล้ว หยุดตัดสินใจและไม่ส่งคำสั่ง")
        if refusal is not None:
            if refusal.code not in self._seen_refusal:
                self._seen_refusal.add(refusal.code)
                ex.alerts.push(Severity.CRITICAL, f"bot-{self.inner.tag}-config", f"{refusal.message_th} ยังรัน revision {self.rev.revision} ต่อ")
            self.note = f"newest approved revision refused: {refusal.code}"
            return
        if newest is not None and newest.revision > self.rev.revision:
            if (newest.config.allocation, newest.config.strategy, newest.config.account) != \
                    (self.rev.config.allocation, self.rev.config.strategy, self.rev.config.account):
                key = f"fixed-{newest.revision}"
                if key not in self._seen_refusal:
                    self._seen_refusal.add(key)
                    ex.alerts.push(Severity.CRITICAL, f"bot-{self.inner.tag}-config",
                                   f"revision {newest.revision} ของ {self.rev.config.name} เปลี่ยนเงิน กลยุทธ์ หรือบัญชี ซึ่งต้องเป็นบอทใหม่ ไม่ใช้")
                return
            if self.pending is None or self.pending.revision != newest.revision:
                ex.log(f"{self.inner.tag}: revision {newest.revision} approved; switching when the book is flat")
            self.pending = newest

    def _flat(self, ex: Executor) -> bool | None:
        if any(o.intent.book == self.book and not o.is_terminal for o in ex.orders.values()) or \
                any(i.book == self.book for _, i in ex.waiting.values()):
            return False
        q, rules = ex.quote(), ex.rules
        if q is None or rules is None:
            return None
        held, _ = self.store.position(self.book, self.inner.strategy.instrument, self.inner.base, self.inner.quote_asset)
        return held * q.bid < rules.min_notional

    def _try_switch(self, ex: Executor) -> None:
        new = self.pending
        assert new is not None
        flat = self._flat(ex)
        if not flat:
            self.note = f"revision {new.revision} waiting: " + ("the book still holds a position or an order"
                                                                if flat is False else "no fresh quote to check the book")
            return
        old = self.rev
        self.rev, self.pending, self.note = new, None, ""
        self.inner.label, self.inner.send_orders = new.config.name, new.config.mode == "TESTNET"
        ex.alerts.push(Severity.INFO, f"bot-{self.inner.tag}-switch", f"{new.config.name} เปลี่ยนจาก revision {old.revision} "
                       f"เป็น {new.revision} แล้ว (โหมด {new.config.mode})")
        ex.log(f"{self.inner.tag}: now on revision {new.revision} ({new.config.mode}, hash {new.hash[:12]})")

    def body(self) -> dict[str, Any]:
        return {**self.inner.body(), "bot": self.identity}

    def close(self) -> None:
        self.inner.close()
