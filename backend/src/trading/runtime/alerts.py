"""Alerts to Telegram, with a local queue (testnet-development-plan.md §2.3, RT42).

``AlertQueue.push`` never raises and never blocks on the network for long: an
alert is appended to a JSONL file first, then the queue tries to send what
is pending in order. Anything that fails stays in the file for the next
flush, so a Telegram outage cannot stop Guardian or the Gateway, and nothing
is lost across a restart. The same key is not re-sent inside its cooldown.
The bot token is part of the request URL, so every error passes through
``mask`` before it is kept or logged.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Protocol

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_LIMIT = 4096
_TOKEN_SHAPE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


COOLDOWN = {Severity.CRITICAL: timedelta(minutes=5), Severity.WARNING: timedelta(minutes=15), Severity.INFO: timedelta(0)}
ICON = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵"}
# What Note reads first: how urgent it is, in Thai. The texts say what happened, whether it is dangerous and what to do.
LABEL = {Severity.CRITICAL: "ด่วน ต้องดู", Severity.WARNING: "เตือน", Severity.INFO: "แจ้งให้ทราบ"}
LOCAL_TZ = timezone(timedelta(hours=7))   # Note reads alerts in Thai time; the queue file keeps ISO UTC


@dataclass(frozen=True)
class Alert:
    severity: Severity
    key: str            # what the alert is about, for the cooldown, e.g. "clock" or "lease"
    text: str
    at: str             # ISO time it was raised

    def render(self, host: str) -> str:
        try:
            when = datetime.fromisoformat(self.at).astimezone(LOCAL_TZ).strftime("%d/%m %H:%M:%S") + " เวลาไทย"
        except ValueError:
            when = self.at
        return f"{ICON[self.severity]} {LABEL[self.severity]} · {host}\n{self.text}\n{when}"[:TELEGRAM_LIMIT]


def mask(text: str, token: str | None = None) -> str:
    if token:
        text = text.replace(token, "<redacted>")
    return _TOKEN_SHAPE.sub("<redacted>", text)


class Sink(Protocol):
    def send(self, text: str) -> None: ...


class TelegramSink:
    def __init__(self, token: str, chat_id: str, timeout: float = 10) -> None:
        self._token, self._chat_id, self._timeout = token, chat_id, timeout

    def __repr__(self) -> str:
        return "TelegramSink(token=<redacted>)"

    def send(self, text: str) -> None:
        body = json.dumps({"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/bot{self._token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                ok = json.load(r).get("ok")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(mask(f"telegram send failed: {e}", self._token)) from None
        if not ok:
            raise RuntimeError("telegram send failed: not ok")


class AlertQueue:
    def __init__(self, path: Path, sink: Sink | None, host: str, now: Callable[[], datetime],
                 log: Callable[[str], None] = print) -> None:
        self.path, self.sink, self.host, self.now, self.log = path, sink, host, now, log
        self._last: dict[str, datetime] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def push(self, severity: Severity, key: str, text: str) -> None:
        now = self.now()
        last = self._last.get(key)
        if last is not None and now - last < COOLDOWN[severity]:
            return
        self._last[key] = now
        alert = Alert(severity, key, text, now.isoformat(timespec="seconds"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(alert), ensure_ascii=False) + "\n")
        try:
            self.log(f"alert {alert.severity.value} {key}: {text}")
        except UnicodeError:
            pass                                       # a console that cannot show the text must not drop the alert
        self.flush()

    def pending(self) -> list[Alert]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out.append(Alert(Severity(d["severity"]), d["key"], d["text"], d["at"]))
        return out

    def flush(self) -> int:
        """Send pending alerts in order; stop at the first failure and keep the rest. Returns how many were sent."""
        if self.sink is None:
            return 0
        todo = self.pending()
        sent = 0
        for a in todo:
            try:
                self.sink.send(a.render(self.host))
            except Exception as e:  # noqa: BLE001 - an alert channel failure must never stop the worker
                self.log(mask(f"alert send failed, kept for retry: {e}"))
                break
            sent += 1
        if sent:
            rest = todo[sent:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(asdict(a), ensure_ascii=False) + "\n" for a in rest), encoding="utf-8")
            tmp.replace(self.path)
        return sent
