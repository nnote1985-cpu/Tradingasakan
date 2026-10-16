"""Account safety state (BUILDPLAN v4.2 §13.2, §14.2, §15.1).

A restart always starts in RECOVERING with new entries blocked. REDUCING sits
between the spot.v1 alert and HALT: exits continue, no new exposure, and it
returns to RUNNING only after a cooldown. HALTED is latched: only a manual,
authorized resume (through RECOVERING) clears it, never midnight or a restart.
Each HALT raises the safety epoch, so an intent created under an older epoch
can no longer open exposure (§14.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class SafetyState(StrEnum):
    RECOVERING = "RECOVERING"
    RUNNING = "RUNNING"
    SOFT_PAUSED = "SOFT_PAUSED"
    REDUCING = "REDUCING"
    HALTING = "HALTING"
    HALTED = "HALTED"


A = SafetyState
SAFETY_TRANSITIONS: dict[SafetyState, frozenset[SafetyState]] = {
    A.RECOVERING: frozenset({A.RUNNING, A.HALTING}),
    A.RUNNING: frozenset({A.SOFT_PAUSED, A.REDUCING, A.HALTING}),
    A.SOFT_PAUSED: frozenset({A.RUNNING, A.REDUCING, A.HALTING}),
    A.REDUCING: frozenset({A.RUNNING, A.SOFT_PAUSED, A.HALTING}),
    A.HALTING: frozenset({A.HALTED}),
    A.HALTED: frozenset({A.RECOVERING}),
}


class IllegalSafetyTransition(ValueError):
    pass


@dataclass
class AccountSafety:
    state: SafetyState = SafetyState.RECOVERING
    epoch: int = 1
    halt_reason: str | None = None
    reducing_until: datetime | None = None
    log: list[tuple[datetime, SafetyState, str]] = field(default_factory=list)

    @property
    def allows_entries(self) -> bool:
        return self.state is SafetyState.RUNNING

    def _go(self, new: SafetyState, at: datetime, reason: str) -> None:
        if new not in SAFETY_TRANSITIONS[self.state]:
            raise IllegalSafetyTransition(f"{self.state} -> {new}")
        self.state = new
        self.log.append((at, new, reason))

    def recovered(self, at: datetime, *, reconciled: bool) -> None:
        """Finish RECOVERING. Needs a clean reconciliation and no HALT latch."""
        if not reconciled:
            raise IllegalSafetyTransition("cannot leave RECOVERING without a clean reconciliation")
        if self.halt_reason is not None:
            raise IllegalSafetyTransition("HALT latch is set; an operator resume is required")
        self._go(SafetyState.RUNNING, at, "recovered")

    def soft_pause(self, at: datetime, reason: str) -> None:
        self._go(SafetyState.SOFT_PAUSED, at, reason)

    def reduce(self, at: datetime, reason: str, cooldown: timedelta) -> None:
        self._go(SafetyState.REDUCING, at, reason)
        self.reducing_until = at + cooldown

    def maybe_end_reducing(self, at: datetime) -> bool:
        if self.state is SafetyState.REDUCING and self.reducing_until is not None and at >= self.reducing_until:
            self._go(SafetyState.RUNNING, at, "cooldown over")
            self.reducing_until = None
            return True
        return False

    def unpause(self, at: datetime) -> None:
        if self.state is not SafetyState.SOFT_PAUSED:
            raise IllegalSafetyTransition(f"not paused: {self.state}")
        self._go(SafetyState.RUNNING, at, "unpaused")

    def halt(self, at: datetime, reason: str) -> None:
        """Latch HALT and raise the epoch. Idempotent while already halting or halted."""
        if self.state in (SafetyState.HALTING, SafetyState.HALTED):
            return
        self.halt_reason = reason
        self.epoch += 1
        self._go(SafetyState.HALTING, at, reason)

    def halted_flat_confirmed(self, at: datetime) -> None:
        self._go(SafetyState.HALTED, at, "flat confirmed on the exchange")

    def operator_resume(self, at: datetime, operator: str) -> None:
        """Manual resume: clears the latch and goes to RECOVERING; RUNNING still needs reconciliation."""
        if not operator:
            raise IllegalSafetyTransition("resume needs a named operator")
        self._go(SafetyState.RECOVERING, at, f"resume by {operator}")
        self.halt_reason = None
