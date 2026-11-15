"""Configured bots: save revisions, submit, approve, reject, retire (docs/m4-bot-config-plan.md §3.3–3.4).

The API only writes rows in ``trading.bots`` and ``trading.bot_approvals``. It never talks to a
worker and holds no trading keys: a worker started with ``WORKER_BOT=on`` reads the approved
revision itself and checks its hash. ``running`` and ``halted`` come from the worker's heartbeat.

Every check that matters is done here, inside one transaction holding an advisory lock per account,
and again by the database triggers: the browser's own checks are only for faster feedback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from trading.bots import config, templates
from trading.bots.config import AccountFunds, ConfigInvalid, FieldError

ACTIONS = ("submit", "approve", "reject", "retire")


class SaveBot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    request_id: UUID = Field(default_factory=uuid4)
    config: dict[str, Any]


class BotEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(default="", max_length=500)


class BotsUnavailable(Exception):
    """No database, or the bots migration is not applied yet."""


class BotNotFound(Exception):
    pass


class BotRefused(Exception):
    """A rule refused the request; ``errors`` name the fields (409 for state, 422 for values)."""

    def __init__(self, errors: list[FieldError], status: int = 409) -> None:
        super().__init__("; ".join(e.code for e in errors))
        self.errors, self.status = errors, status


def refused(field: str, code: str, message_th: str, status: int = 409) -> BotRefused:
    return BotRefused([FieldError(field, code, message_th)], status)


def template_list() -> list[dict[str, Any]]:
    return [t.public() for t in templates.TEMPLATES.values()]


def flat(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(flat(v, f"{prefix}.{k}" if prefix else str(k)))
        return out if out or not prefix else {prefix: {}}
    return {prefix: value}


def diff(before: dict[str, Any] | None, after: dict[str, Any]) -> list[dict[str, Any]]:
    """Changed fields between two config bodies, as dotted paths."""
    a, b = flat(before or {}), flat(after)
    return [{"field": k, "before": a.get(k), "after": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


# --- SQL -------------------------------------------------------------------------------------

BOT_COLS = "bot_id, revision, account, body, hash, saved_by, saved_at"
EVENT_COLS = "bot_id, revision, hash, action, actor, at, note"

HEARTBEATS_SQL = """
select account, updated_at, body -> 'execution' ->> 'safety', body -> 'execution' -> 'bot',
       body -> 'execution' -> 'r10a' ->> 'book'
from trading.worker_heartbeats
"""

FUNDS_SQL = """
select b.free, b.as_of, r.verdict
from trading.exec_account_epochs e
left join trading.exec_balances b on b.account = e.account and b.epoch = e.epoch and b.asset = 'USDT'
left join lateral (select verdict from trading.exec_reconciliations x where x.account = e.account
                   order by checked_at desc, id desc limit 1) r on true
where e.account = %s and e.status = 'OPEN'
"""

ACCOUNTS_SQL = """
select h.account, b.free, b.as_of, r.verdict
from trading.worker_heartbeats h
left join trading.exec_account_epochs e on e.account = h.account and e.status = 'OPEN'
left join trading.exec_balances b on b.account = h.account and b.epoch = e.epoch and b.asset = 'USDT'
left join lateral (select verdict from trading.exec_reconciliations x where x.account = h.account
                   order by checked_at desc, id desc limit 1) r on true
order by h.account
"""

ACTIVE_ON_ACCOUNT_SQL = """
select b.bot_id, (select body ->> 'name' from trading.bots x where x.bot_id = b.bot_id order by revision desc limit 1)
from (select distinct bot_id from trading.bots where account = %s) b
where not exists (select 1 from trading.bot_approvals r where r.bot_id = b.bot_id and r.action = 'retire')
"""


def _bot_row(r: tuple[Any, ...]) -> dict[str, Any]:
    bot_id, rev, account, body, h, by, at = r
    return {"bot_id": str(bot_id), "revision": rev, "account": account, "config": body, "hash": h, "saved_by": str(by),
            "saved_at": at.isoformat()}


def _event_row(r: tuple[Any, ...]) -> dict[str, Any]:
    bot_id, rev, h, action, actor, at, note = r
    return {"bot_id": str(bot_id), "revision": rev, "hash": h, "action": action, "actor": str(actor), "at": at.isoformat(),
            "note": note}


def summarize(revisions: list[dict[str, Any]], events: list[dict[str, Any]], beat: dict[str, Any] | None) -> dict[str, Any]:
    """One bot: latest revision, approved revision, what the worker reports, and the derived status."""
    latest = revisions[-1]
    bot_id = UUID(latest["bot_id"])
    approves = [e for e in events if e["action"] == "approve"]
    approved = approves[-1] if approves else None
    retired = next((e for e in events if e["action"] == "retire"), None)
    latest_events = {e["action"]: e for e in events if e["revision"] == latest["revision"]}
    running = None
    reported = (beat or {}).get("bot") or None
    if reported and reported.get("bot_id") == str(bot_id):
        running = {"revision": reported.get("revision"), "hash": reported.get("hash"), "mode": reported.get("mode"),
                   "pending_revision": reported.get("pending_revision"), "note": reported.get("note"),
                   "safety": (beat or {}).get("safety"), "reported_at": (beat or {}).get("updated_at")}
    if retired:
        status = "retired"
    elif running:
        status = "halted" if running["safety"] == "HALTED" else "running"
    elif approved:
        status = "approved"
    elif "submit" in latest_events and not ({"approve", "reject"} & set(latest_events)):
        status = "submitted"
    else:
        status = "draft"
    verdict = latest_events.get("approve") or latest_events.get("reject")
    return {
        "bot_id": str(bot_id), "book": config.book_for(bot_id), "account": latest["account"], "name": latest["config"].get("name"),
        "status": status,
        "latest": {**latest, "submitted": "submit" in latest_events,
                   "verdict": None if verdict is None else {"action": verdict["action"], "actor": verdict["actor"],
                                                            "at": verdict["at"], "note": verdict["note"]}},
        "approved": None if approved is None else {"revision": approved["revision"], "hash": approved["hash"],
                                                   "actor": approved["actor"], "at": approved["at"]},
        "running": running,
        "retired": None if retired is None else {"actor": retired["actor"], "at": retired["at"], "note": retired["note"]},
    }


class BotStore(Protocol):
    async def list_bots(self) -> list[dict[str, Any]]: ...
    async def get_bot(self, bot_id: UUID) -> dict[str, Any]: ...
    async def accounts(self) -> list[dict[str, Any]]: ...
    async def save(self, request: SaveBot, author: UUID) -> dict[str, Any]: ...
    async def act(self, bot_id: UUID, revision: int | None, action: str, actor: UUID, note: str) -> dict[str, Any]: ...


class NoBots:
    async def list_bots(self) -> list[dict[str, Any]]:
        raise BotsUnavailable

    async def get_bot(self, bot_id: UUID) -> dict[str, Any]:
        raise BotsUnavailable

    async def accounts(self) -> list[dict[str, Any]]:
        raise BotsUnavailable

    async def save(self, request: SaveBot, author: UUID) -> dict[str, Any]:
        raise BotsUnavailable

    async def act(self, bot_id: UUID, revision: int | None, action: str, actor: UUID, note: str) -> dict[str, Any]:
        raise BotsUnavailable


class PgBotStore:
    def __init__(self, pool: AsyncConnectionPool, now: Any = None) -> None:
        self.pool = pool
        self.now = now or (lambda: datetime.now(UTC))

    # --- reading -----------------------------------------------------------------------------

    @staticmethod
    async def _beats(conn: AsyncConnection) -> dict[str, dict[str, Any]]:
        rows = await (await conn.execute(HEARTBEATS_SQL)).fetchall()
        return {a: {"updated_at": None if at is None else at.isoformat(), "safety": safety, "bot": bot, "strategy_book": book}
                for a, at, safety, bot, book in rows}

    @staticmethod
    async def _bot(conn: AsyncConnection, bot_id: UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        revs = [_bot_row(r) for r in await (await conn.execute(
            f"select {BOT_COLS} from trading.bots where bot_id = %s order by revision", (bot_id,))).fetchall()]
        events = [_event_row(r) for r in await (await conn.execute(
            f"select {EVENT_COLS} from trading.bot_approvals where bot_id = %s order by id", (bot_id,))).fetchall()]
        return revs, events

    async def list_bots(self) -> list[dict[str, Any]]:
        try:
            async with self.pool.connection() as conn:
                beats = await self._beats(conn)
                revs = [_bot_row(r) for r in await (await conn.execute(
                    f"select {BOT_COLS} from trading.bots order by bot_id, revision")).fetchall()]
                events = [_event_row(r) for r in await (await conn.execute(
                    f"select {EVENT_COLS} from trading.bot_approvals order by id")).fetchall()]
        except pg_errors.UndefinedTable as e:
            raise BotsUnavailable from e
        out = []
        for bot_id in dict.fromkeys(r["bot_id"] for r in revs):
            mine = [r for r in revs if r["bot_id"] == bot_id]
            out.append(summarize(mine, [e for e in events if e["bot_id"] == bot_id], beats.get(mine[-1]["account"])))
        return sorted(out, key=lambda b: b["latest"]["saved_at"], reverse=True)

    async def get_bot(self, bot_id: UUID) -> dict[str, Any]:
        try:
            async with self.pool.connection() as conn:
                revs, events = await self._bot(conn, bot_id)
                beats = await self._beats(conn)
        except pg_errors.UndefinedTable as e:
            raise BotsUnavailable from e
        if not revs:
            raise BotNotFound
        history = [{**r, "diff": diff(revs[i - 1]["config"] if i else None, r["config"])} for i, r in enumerate(revs)]
        return {**summarize(revs, events, beats.get(revs[-1]["account"])), "revisions": history, "events": events}

    @staticmethod
    async def _funds(conn: AsyncConnection, account: str) -> AccountFunds:
        row = await (await conn.execute(FUNDS_SQL, (account,))).fetchone()
        if row is None or row[0] is None:
            return AccountFunds(None, None, None if row is None else row[2])
        return AccountFunds(Decimal(row[0]), row[1], row[2])

    async def accounts(self) -> list[dict[str, Any]]:
        """Accounts a worker has reported, their last reconciled USDT, and whether a new bot may use them."""
        try:
            async with self.pool.connection() as conn:
                beats = await self._beats(conn)
                rows = await (await conn.execute(ACCOUNTS_SQL)).fetchall()
                out = []
                for account, free, as_of, verdict in rows:
                    why = await self._occupied(conn, account, None, beats)
                    out.append({"account": account, "usdt_free": None if free is None else str(free),
                                "balance_as_of": None if as_of is None else as_of.isoformat(), "reconcile": verdict,
                                "available": why is None, "reason_th": why})
                return out
        except pg_errors.UndefinedTable as e:
            raise BotsUnavailable from e

    # --- rules ---------------------------------------------------------------------------------

    @staticmethod
    async def _occupied(conn: AsyncConnection, account: str, bot_id: UUID | None, beats: dict[str, dict[str, Any]]) -> str | None:
        """Why another bot cannot use ``account`` (one bot per account), or None."""
        for other, name in await (await conn.execute(ACTIVE_ON_ACCOUNT_SQL, (account,))).fetchall():
            if other != bot_id:
                return f"บัญชี {account} มีบอท \"{name}\" อยู่แล้ว (หนึ่งบัญชีหนึ่งบอท)"
        beat = beats.get(account) or {}
        if beat.get("strategy_book") and not beat.get("bot"):
            return f"บัญชี {account} รันกลยุทธ์ {beat['strategy_book']} ที่เปิดด้วย env บน worker อยู่"
        return None

    async def save(self, request: SaveBot, author: UUID) -> dict[str, Any]:
        try:
            c = config.parse(request.config)
        except ConfigInvalid as e:
            raise BotRefused(e.errors, 422) from e
        body, digest = config.normalize(c)
        bot_id = request.bot_id or uuid4()
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"trading.bots:{c.account}",))
                previous = await (await conn.execute(f"select {BOT_COLS}, request_id from trading.bots where request_id = %s",
                                                     (request.request_id,))).fetchone()
                if previous is not None:
                    if previous[4] != digest or previous[5] != author or (request.bot_id and previous[0] != request.bot_id):
                        raise refused("request_id", "request_reused", "request id นี้ถูกใช้กับข้อมูลอื่นแล้ว")
                    return _bot_row(previous[:7])
                revs, events = await self._bot(conn, bot_id)
                current = revs[-1]["revision"] if revs else 0
                if current != request.expected_revision or (request.bot_id is not None and current == 0):
                    raise refused("expected_revision", "stale_revision", "บอทนี้มี revision ใหม่กว่าแล้ว โหลดใหม่ก่อนบันทึก")
                if any(e["action"] == "retire" for e in events):
                    raise refused("bot_id", "retired", "บอทนี้ปลดแล้ว บันทึก revision ใหม่ไม่ได้")
                if revs and revs[-1]["account"] != c.account:
                    raise refused("account", "account_fixed", f"บอทนี้ผูกกับบัญชี {revs[-1]['account']} สร้างบอทใหม่ถ้าจะใช้บัญชีอื่น", 422)
                if not revs and (why := await self._occupied(conn, c.account, bot_id, await self._beats(conn))):
                    raise refused("account", "account_in_use", why)
                if c.source_draft is not None and await (await conn.execute(
                        "select 1 from trading.bot_profile_drafts where profile_id = %s and revision = %s",
                        (c.source_draft.profile_id, c.source_draft.revision))).fetchone() is None:
                    raise refused("source_draft", "draft_not_found", "ไม่พบร่างต้นทาง", 422)
                row = await (await conn.execute(
                    f"insert into trading.bots (bot_id, revision, account, body, hash, saved_by, request_id) "
                    f"values (%s, %s, %s, %s, %s, %s, %s) returning {BOT_COLS}",
                    (bot_id, current + 1, c.account, Jsonb(body), digest, author, request.request_id))).fetchone()
        except pg_errors.UniqueViolation as e:
            raise refused("expected_revision", "stale_revision", "บอทนี้มี revision ใหม่กว่าแล้ว โหลดใหม่ก่อนบันทึก") from e
        except pg_errors.UndefinedTable as e:
            raise BotsUnavailable from e
        assert row is not None
        return _bot_row(row)

    async def act(self, bot_id: UUID, revision: int | None, action: str, actor: UUID, note: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError(action)
        try:
            async with self.pool.connection() as conn, conn.transaction():
                revs, events = await self._bot(conn, bot_id)
                if not revs:
                    raise BotNotFound
                account = revs[-1]["account"]
                await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"trading.bots:{account}",))
                revs, events = await self._bot(conn, bot_id)            # again, under the lock
                if any(e["action"] == "retire" for e in events):
                    raise refused("bot_id", "retired", "บอทนี้ปลดแล้ว")
                beats = await self._beats(conn)
                target = revs[-1] if revision is None else next((r for r in revs if r["revision"] == revision), None)
                if target is None:
                    raise BotNotFound
                await self._check(conn, action, target, revs, events, beats)
                await conn.execute("insert into trading.bot_approvals (bot_id, revision, hash, action, actor, note) "
                                   "values (%s, %s, %s, %s, %s, %s)", (bot_id, target["revision"], target["hash"], action, actor, note))
                revs, events = await self._bot(conn, bot_id)
        except pg_errors.UniqueViolation as e:
            raise refused("revision", "already_decided", "revision นี้ถูกส่งหรือตัดสินไปแล้ว") from e
        except pg_errors.RaiseException as e:               # the database's own rules (role, one bot per account)
            raise refused("revision", "database_refused", str(e.diag.message_primary or e)) from e
        except pg_errors.UndefinedTable as e:
            raise BotsUnavailable from e
        return summarize(revs, events, beats.get(account))

    async def _check(self, conn: AsyncConnection, action: str, target: dict[str, Any], revs: list[dict[str, Any]],
                     events: list[dict[str, Any]], beats: dict[str, dict[str, Any]]) -> None:
        rev = target["revision"]
        mine = {e["action"] for e in events if e["revision"] == rev}
        latest = rev == revs[-1]["revision"]
        if action == "submit":
            if not latest:
                raise refused("revision", "not_latest", "ส่งได้เฉพาะ revision ล่าสุด")
            if "submit" in mine:
                raise refused("revision", "already_submitted", "revision นี้ส่งอนุมัติแล้ว")
            return
        if action in ("approve", "reject"):
            if not latest:
                raise refused("revision", "not_latest", "มี revision ใหม่กว่า ตัดสินได้เฉพาะ revision ล่าสุด")
            if "submit" not in mine:
                raise refused("revision", "not_submitted", "revision นี้ยังไม่ได้ส่งอนุมัติ")
            if {"approve", "reject"} & mine:
                raise refused("revision", "already_decided", "revision นี้ตัดสินไปแล้ว")
            if action == "reject":
                return
            try:                                            # the stored body must still pass today's rules and hash
                c = config.parse(target["config"])
            except ConfigInvalid as e:
                raise BotRefused(e.errors, 422) from e
            if config.normalize(c)[1] != target["hash"]:
                raise refused("hash", "hash_mismatch", "hash ของ revision นี้ไม่ตรงกับค่าที่คำนวณใหม่ ห้าม approve")
            if why := await self._occupied(conn, c.account, UUID(target["bot_id"]), beats):
                raise refused("account", "account_in_use", why)
            errs = config.approval_errors(c, await self._funds(conn, c.account), self.now())
            if errs:
                raise BotRefused(errs, 409)
            return
        # retire: only when no worker runs it, or its account is HALTED (Guardian has sold the book)
        beat = beats.get(target["account"]) or {}
        reported = beat.get("bot") or {}
        if reported.get("bot_id") == target["bot_id"] and beat.get("safety") != "HALTED":
            raise refused("bot_id", "still_running", "worker ยังรันบอทนี้อยู่ ให้ HALT บัญชีก่อน (Guardian ขายออก) แล้วจึงปลด")
