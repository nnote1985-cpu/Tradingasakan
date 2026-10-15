"""Send an alert to Telegram, or list chat ids the bot has seen.

    echo "ข้อความ" | python3 scripts/telegram_notify.py
    python3 scripts/telegram_notify.py --text-file /tmp/alert.txt
    python3 scripts/telegram_notify.py --chat-ids

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment (cloud
environment settings, or .env.local loaded by the shell on a local machine).
Standard library only, so it runs without the project's virtualenv. The token
is part of the request URL, so it is never printed or included in errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.telegram.org"
LIMIT = 4096                             # Telegram's maximum message length


class TelegramError(RuntimeError):
    pass


def _call(token: str, method: str, payload: dict | None = None, timeout: float = 20) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            desc = json.load(e).get("description", "")
        except ValueError:
            desc = ""
        raise TelegramError(f"{method} failed: HTTP {e.code} {desc}".strip()) from None
    except urllib.error.URLError as e:
        raise TelegramError(f"{method} failed: {e.reason}") from None
    if not body.get("ok"):
        raise TelegramError(f"{method} failed: {body.get('description', 'unknown error')}")
    return body


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    """Split on line boundaries so each piece fits one message."""
    out: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out


def send(token: str, chat_id: str, text: str) -> int:
    parts = chunks(text)
    for part in parts:
        _call(token, "sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": True})
    return len(parts)


def seen_chats(token: str) -> list[dict]:
    updates = _call(token, "getUpdates").get("result", [])
    chats: dict[int, dict] = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = {"id": chat["id"], "type": chat.get("type"),
                                 "name": chat.get("title") or chat.get("username") or chat.get("first_name")}
    return list(chats.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", help="message file (default: stdin)")
    ap.add_argument("--chat-ids", action="store_true", help="list chats that messaged the bot recently")
    args = ap.parse_args(argv)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        return 2
    try:
        if args.chat_ids:
            print(json.dumps(seen_chats(token), ensure_ascii=False, indent=2))
            return 0
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not chat_id:
            print("TELEGRAM_CHAT_ID is not set (run with --chat-ids after messaging the bot)", file=sys.stderr)
            return 2
        text = open(args.text_file, encoding="utf-8").read() if args.text_file else sys.stdin.read()
        if not text.strip():
            print("empty message, nothing sent", file=sys.stderr)
            return 2
        print(f"sent {send(token, chat_id, text)} message(s)")
        return 0
    except TelegramError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
