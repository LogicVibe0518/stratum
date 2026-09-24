"""Circuit breaker.

A provider that is failing should be skipped quickly, not retried on every request
while designers wait. Three states: closed (normal), open (skip it), half-open (let
one probe through and see).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

FAILURE_THRESHOLD = 5  # failures inside the window before the circuit opens
WINDOW_S = 60.0
COOLDOWN_S = 30.0  # how long to stay open before probing again


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class Breaker:
    """One breaker per provider. Time is injectable so tests need no sleeps."""

    now: object = field(default=time.monotonic)
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def _clock(self) -> float:
        clock = self.now
        return float(clock())  # type: ignore[operator]

    @property
    def state(self) -> State:
        if self.opened_at is None:
            return State.CLOSED
        if self._clock() - self.opened_at >= COOLDOWN_S:
            return State.HALF_OPEN
        return State.OPEN

    def allows(self) -> bool:
        return self.state is not State.OPEN

    def record_success(self) -> None:
        self.failures.clear()
        self.opened_at = None

    def record_failure(self) -> None:
        now = self._clock()
        self.failures = [t for t in self.failures if now - t < WINDOW_S]
        self.failures.append(now)
        if len(self.failures) >= FAILURE_THRESHOLD:
            self.opened_at = now
            self.failures.clear()
