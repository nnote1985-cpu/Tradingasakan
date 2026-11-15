"""What a worker started with ``WORKER_BOT=on`` reads: the approved revision of its account's bot (m4-bot-config-plan.md §3.5).

The worker trusts nothing it did not check itself: it parses the stored body with today's rules,
recomputes the hash, and compares it with the hash stored on the revision and on the approve event.
A draft, a submitted or a rejected revision is never returned, however new.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from trading.bots import config
from trading.bots.config import BotConfig, ConfigInvalid

ACTIVE_SQL = """
select distinct b.bot_id from trading.bots b
join trading.bot_approvals a on a.bot_id = b.bot_id and a.action = 'approve'
where b.account = %s
  and not exists (select 1 from trading.bot_approvals r where r.bot_id = b.bot_id and r.action = 'retire')
order by b.bot_id
"""

APPROVED_SQL = """
select b.bot_id, b.revision, b.account, b.body, b.hash, a.hash, a.actor, a.at
from trading.bot_approvals a join trading.bots b on b.bot_id = a.bot_id and b.revision = a.revision
where a.bot_id = %s and a.action = 'approve'
order by a.revision desc limit 1
"""

RETIRED_SQL = "select 1 from trading.bot_approvals where bot_id = %s and action = 'retire'"


class BotLoadRefused(Exception):
    """The worker must not start (or must not switch): ``code`` says why, the message is Thai for the alert."""

    def __init__(self, code: str, message_th: str) -> None:
        super().__init__(f"{code}: {message_th}")
        self.code, self.message_th = code, message_th


@dataclass(frozen=True)
class ApprovedRevision:
    bot_id: UUID
    revision: int
    hash: str
    config: BotConfig
    approved_by: str
    approved_at: datetime

    @property
    def book(self) -> str:
        return config.book_for(self.bot_id)

    def identity(self) -> dict[str, Any]:
        return {"bot_id": str(self.bot_id), "revision": self.revision, "hash": self.hash, "mode": self.config.mode,
                "name": self.config.name, "book": self.book}


def verify(row: tuple[Any, ...], account: str) -> ApprovedRevision:
    bot_id, revision, acct, body, stored, approved_hash, actor, at = row
    where = f"บอท {bot_id} revision {revision}"
    if acct != account:
        raise BotLoadRefused("wrong_account", f"{where} เป็นของบัญชี {acct} ไม่ใช่ {account}")
    try:
        c = config.parse(body)
    except ConfigInvalid as e:
        raise BotLoadRefused("config_invalid", f"{where} ไม่ผ่านการตรวจของ worker ({e})") from e
    _, computed = config.normalize(c)
    if not (computed == stored == approved_hash):
        raise BotLoadRefused("hash_mismatch", f"{where}: hash ไม่ตรง (คำนวณได้ {computed[:12]}, เก็บไว้ {str(stored)[:12]}, "
                                              f"ตอน approve {str(approved_hash)[:12]}) worker จะไม่ใช้ config นี้")
    if c.account != account:
        raise BotLoadRefused("wrong_account", f"{where} ตั้งบัญชี {c.account} ไม่ใช่ {account}")
    return ApprovedRevision(bot_id, revision, stored, c, str(actor), at)


def load_for_account(conn: Any, account: str) -> ApprovedRevision:
    """The approved revision of the one bot on ``account``; refuses none or more than one."""
    bots = [r[0] for r in conn.execute(ACTIVE_SQL, (account,)).fetchall()]
    if not bots:
        raise BotLoadRefused("no_approved_bot", f"บัญชี {account} ไม่มีบอทที่ approve แล้ว (ร่างหรือรออนุมัติไม่ถูกใช้)")
    if len(bots) > 1:
        raise BotLoadRefused("two_bots", f"บัญชี {account} มีบอทที่ approve แล้ว {len(bots)} ตัว (หนึ่งบัญชีหนึ่งบอท)")
    row = conn.execute(APPROVED_SQL, (bots[0],)).fetchone()
    return verify(row, account)


def newest(conn: Any, bot_id: UUID, account: str) -> tuple[ApprovedRevision | None, bool, BotLoadRefused | None]:
    """(newest approved revision, retired?, why the newest one cannot be used) for a running bot."""
    retired = conn.execute(RETIRED_SQL, (bot_id,)).fetchone() is not None
    row = conn.execute(APPROVED_SQL, (bot_id,)).fetchone()
    if row is None:
        return None, retired, BotLoadRefused("no_approved_bot", f"บอท {bot_id} ไม่มี revision ที่ approve แล้ว")
    try:
        return verify(row, account), retired, None
    except BotLoadRefused as e:
        return None, retired, e
