"""Circuit breaker for provider endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    """Closed → open after `failure_threshold` failures; half-open after `reset_ms`."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_ms: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_ms = reset_ms
        self._clock = clock or time.monotonic
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        # half-open probe once reset window elapses
        return (self._clock() - self.opened_at) * 1000 >= self.reset_ms

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = self._clock()
