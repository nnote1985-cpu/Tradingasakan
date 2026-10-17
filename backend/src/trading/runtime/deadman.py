"""External dead-man switch (testnet-development-plan.md §2.3, RT41).

The worker pings an uptime monitor outside the Host once a minute. If the
pings stop for five minutes (worker stopped, PC off, network down), that
service sends the Telegram alert itself, since nothing on a dead Host can.
The ping URL holds the check's secret id, so it is masked in logs.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

PING_EVERY_S = 60


def mask_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.hostname}/…"


def _get(url: str, timeout: float) -> int:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tradingasakan-worker"}), timeout=timeout) as r:
        return int(r.status)


class DeadMan:
    def __init__(self, url: str, get: Callable[[str, float], int] = _get, timeout: float = 10) -> None:
        if urllib.parse.urlsplit(url).scheme != "https":
            raise ValueError("DEADMAN_PING_URL must be https")
        self._url, self._get, self._timeout = url, get, timeout

    def __repr__(self) -> str:
        return f"DeadMan({mask_url(self._url)})"

    def ping(self) -> bool:
        try:
            return 200 <= self._get(self._url, self._timeout) < 300
        except (urllib.error.URLError, OSError):
            return False
