"""Daily Telegram summary of every port: value, 1-day P&L and P&L since start.

    uv run python scripts/portfolio_summary.py --dry-run          # print the message, send nothing, keep state
    uv run python scripts/portfolio_summary.py                    # send to Telegram, then save today's values
    uv run python scripts/portfolio_summary.py --state /path.json # where the last report's values are kept

Ports (all read from Supabase, read-only):
    Paper books    trading.equity_snapshots, one row per daily close. 1 day = last close vs the close before it.
                   Since start = vs portfolios.initial_equity; the book's buy & hold twin is shown beside it.
    Testnet bots   trading.exec_book_risk (marked by the workers). Since start = vs the book's allocation.
    Futures Demo   trading.fut_state; since start = sum of trading.fut_income + unrealized P&L.
    For Testnet and Futures, 1 day = value now vs the value saved at the last report (the state file), so the
    first report shows no 1-day number for them.

Environment: DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (the VM's /etc/tradingasakan/futures.env has all three).
Runs from a systemd timer: deploy/systemd/tradingasakan-summary.{service,timer}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import telegram_notify as tg  # noqa: E402

BKK = timezone(timedelta(hours=7))
PAPER_ACTIVE_DAYS = 5         # a paper book with no close for this long has ended (e.g. pathA-p5-r1); younger gaps show ⚠️
BOOK_ACTIVE_DAYS = 7          # a Testnet book not marked for this long is not running (e.g. tn-conformance-v1)
WORKER_STALE = timedelta(hours=2)
FUTURES_STALE = timedelta(minutes=30)

# Short names shown first (the book label follows in brackets), in this order; other books follow by label.
PAPER_HINT = {
    "pathA-r10a-hedge": "3a (R10a + short hedge)",
    "pathA-r10a": "R10a spot",
    "spot-e1-v1": "E1 Donchian H4",
    "futures-r7": "R7 perp",
    "futures-r11": "R11 perp + band",
}
_ORDER = {k: i for i, k in enumerate(PAPER_HINT)}

PAPER_SQL = """
with ranked as (
  select p.label, p.initial_equity, e.at, coalesce(e.economic_equity, e.trading_equity) as equity,
         row_number() over (partition by p.id order by e.at desc) as rn
  from trading.portfolios p join trading.equity_snapshots e on e.portfolio_id = p.id
)
select label, initial_equity, at, equity, rn from ranked where rn <= 2 order by label, rn
"""

BOOK_SQL = """
select r.account, r.book, r.allocation, r.last_equity, r.marked_at,
       (select b.body->>'name' from trading.bots b
         where r.book like 'bot-%' and left(b.bot_id::text, 8) = substr(r.book, 5)
         order by b.revision desc limit 1) as bot_name
from trading.exec_book_risk r order by r.account, r.book
"""

FUTURES_SQL = """
select s.account, s.synced_at, s.margin_balance, s.unrealized_pnl,
       coalesce(jsonb_array_length(s.positions), 0) as n_positions,
       (select coalesce(sum(i.amount), 0) from trading.fut_income i where i.account = s.account) as income
from trading.fut_state s order by s.account
"""


@dataclass(frozen=True)
class Line:
    key: str                 # state key for the 1-day number, e.g. "book:DEFAULT/tn-r10a-code-v1"
    name: str
    value: float
    day: float | None        # P&L over 1 day (None = not known yet)
    total: float
    start: float             # base for the % of total
    extra: str = ""
    warn: str = ""


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def paper_lines(rows: list[tuple], now: datetime) -> tuple[list[Line], str | None]:
    """rows: (label, initial_equity, at, equity, rn) with rn 1 = last close, 2 = the close before."""
    books: dict[str, dict] = {}
    for label, initial, at, equity, rn in rows:
        b = books.setdefault(label, {"initial": _f(initial)})
        b[rn] = (at, _f(equity))
    expected = (now.astimezone(UTC) - timedelta(days=1)).date()     # yesterday's UTC close is due by ~15:20 BKK
    lines, closes = [], set()
    for label in sorted(books):
        prefix, _, kind = label.rpartition("/")
        if kind != "paper":
            continue
        b = books[label]
        at, eq = b[1]
        if now - at > timedelta(days=PAPER_ACTIVE_DAYS):
            continue
        day = eq - b[2][1] if 2 in b else None
        initial = b["initial"] or 10000.0
        extra = ""
        bh = books.get(f"{prefix}/buy_and_hold")
        if bh and bh[1][0] == at:
            bh_init = bh["initial"] or 10000.0
            extra = f"B&H {_pct(bh[1][1] - bh_init, bh_init)}"
        warn = "" if at.date() >= expected else f"ข้อมูลถึง {at.strftime('%d/%m')}"
        closes.add(at.date())
        hint = PAPER_HINT.get(prefix)
        name = f"{hint} · {prefix}" if hint else prefix
        lines.append(Line(f"paper:{prefix}", name, eq, day, eq - initial, initial, extra, warn))
    lines.sort(key=lambda ln: (_ORDER.get(ln.key.removeprefix("paper:"), len(_ORDER)), ln.key))
    close = max(closes).strftime("%d/%m") if closes else None
    return lines, close


def book_lines(rows: list[tuple], now: datetime, prev: dict) -> list[Line]:
    lines = []
    for account, book, allocation, last_equity, marked_at, bot_name in rows:
        if marked_at is None or now - marked_at > timedelta(days=BOOK_ACTIVE_DAYS):
            continue
        key = f"book:{account}/{book}"
        value, alloc = _f(last_equity), _f(allocation)
        day = value - prev[key] if key in prev else None
        name = f"{account} · {bot_name or book}"
        warn = f"worker ไม่อัปเดตตั้งแต่ {_bkk(marked_at)}" if now - marked_at > WORKER_STALE else ""
        lines.append(Line(key, name, value, day, value - alloc, alloc, warn=warn))
    return lines


def futures_lines(rows: list[tuple], now: datetime, prev: dict) -> list[Line]:
    lines = []
    for account, synced_at, margin, upnl, n_pos, income in rows:
        key = f"fut:{account}"
        value = _f(margin)
        total = _f(income) + _f(upnl)
        start = value - total
        day = value - prev[key] if key in prev else None
        extra = f"เปิดอยู่ {n_pos} position" if n_pos else "ไม่มี position"
        warn = f"sync ล่าสุด {_bkk(synced_at)}" if synced_at is None or now - synced_at > FUTURES_STALE else ""
        lines.append(Line(key, account, value, day, total, start, extra, warn))
    return lines


def _bkk(t: datetime | None) -> str:
    return t.astimezone(BKK).strftime("%d/%m %H:%M") if t else "-"


def _money(x: float) -> str:
    return f"{x:+,.2f}"


def _pct(x: float, base: float) -> str:
    return f"{x / base * 100:+.2f}%" if base else "-"


def _dot(x: float | None) -> str:
    if x is None or abs(x) < 0.005:
        return "⚪"
    return "🟢" if x > 0 else "🔴"


def _render_line(ln: Line) -> str:
    day = "1 วัน: ยังไม่มี (เริ่มนับรอบหน้า)" if ln.day is None else \
        f"1 วัน: {_dot(ln.day)} {_money(ln.day)} ({_pct(ln.day, ln.value - ln.day)})"
    out = [f"• {ln.name}",
           f"  มูลค่า {ln.value:,.2f} USDT",
           f"  {day}",
           f"  ตั้งแต่เริ่ม: {_dot(ln.total)} {_money(ln.total)} ({_pct(ln.total, ln.start)})"
           + (f" · {ln.extra}" if ln.extra else "")]
    if ln.warn:
        out.append(f"  ⚠️ {ln.warn}")
    return "\n".join(out)


def render(now: datetime, paper: list[Line], paper_close: str | None, books: list[Line], fut: list[Line]) -> str:
    parts = [f"📊 สรุปพอร์ตประจำวัน {now.astimezone(BKK).strftime('%d/%m/%Y %H:%M')} เวลาไทย"]
    if paper:
        parts.append(f"\n📝 Paper book (ปิดวัน {paper_close}, เริ่ม 10,000)\n" + "\n".join(map(_render_line, paper)))
    if books:
        parts.append("\n🧪 Spot Testnet bot\n" + "\n".join(map(_render_line, books)))
    if fut:
        parts.append("\n📈 Futures Demo\n" + "\n".join(map(_render_line, fut)))
    if not (paper or books or fut):
        parts.append("\nไม่พบพอร์ตที่ทำงานอยู่")
    parts.append("\nทั้งหมดเป็นเงินจำลอง (paper / testnet / demo)")
    return "\n".join(parts)


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("values", {})
    except (OSError, ValueError):
        return {}


def save_state(path: Path, now: datetime, lines: list[Line]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now.isoformat(timespec="seconds"),
                               "values": {ln.key: ln.value for ln in lines}}, indent=1), encoding="utf-8")
    tmp.replace(path)


def build(conn, now: datetime, prev: dict) -> tuple[str, list[Line]]:
    with conn.cursor() as cur:
        cur.execute(PAPER_SQL)
        paper, close = paper_lines(cur.fetchall(), now)
        cur.execute(BOOK_SQL)
        books = book_lines(cur.fetchall(), now, prev)
        cur.execute(FUTURES_SQL)
        fut = futures_lines(cur.fetchall(), now, prev)
    return render(now, paper, close, books, fut), books + fut


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the message; send nothing, keep the state file")
    ap.add_argument("--state", default=os.environ.get("SUMMARY_STATE", "/var/lib/tradingasakan-summary/state.json"))
    args = ap.parse_args(argv)
    db = os.environ.get("DATABASE_URL")
    if not db:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run and not (token and chat):
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (or use --dry-run)", file=sys.stderr)
        return 2
    import psycopg

    state = Path(args.state)
    now = datetime.now(UTC)
    with psycopg.connect(db, autocommit=True, connect_timeout=10) as conn:
        text, live = build(conn, now, load_state(state))
    print(text, flush=True)
    if args.dry_run:
        return 0
    try:
        tg.send(token, chat, text)
    except tg.TelegramError as e:
        print(tg.mask(str(e), token), file=sys.stderr)
        return 1
    save_state(state, now, live)
    print(f"sent; state saved to {state}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
