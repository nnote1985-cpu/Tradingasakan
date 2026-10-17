"""Clock offset to the Testnet server (testnet-development-plan.md §4.2, RT27).

The offset is the server time minus the midpoint of the local send and
receive times. Signed requests carry ``local + offset``, so a steady offset
is harmless; a large one means the Host clock is not synced, which is an
incident. At WARN_MS the worker raises a warning; at PAUSE_MS signed submits
pause until a later check is back under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

WARN_MS = 500
PAUSE_MS = 1000


class ClockStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    PAUSE = "PAUSE"


@dataclass(frozen=True)
class ClockReading:
    offset_ms: float
    rtt_ms: float

    @property
    def status(self) -> ClockStatus:
        a = abs(self.offset_ms)
        return ClockStatus.PAUSE if a >= PAUSE_MS else ClockStatus.WARN if a >= WARN_MS else ClockStatus.OK


def reading(local_before_ms: float, server_ms: float, local_after_ms: float) -> ClockReading:
    if local_after_ms < local_before_ms:
        raise ValueError("local clock went backwards during the check")
    return ClockReading(offset_ms=server_ms - (local_before_ms + local_after_ms) / 2, rtt_ms=local_after_ms - local_before_ms)
