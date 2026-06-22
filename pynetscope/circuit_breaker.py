"""Simple endpoint circuit breaker."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

CircuitState = Literal["closed", "open", "half-open"]


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_successes: int = 1
    on_state_change: Callable[[CircuitState], None] | None = None

    state: CircuitState = "closed"
    failure_count: int = 0
    success_count: int = 0
    opened_at: float = 0.0

    def allow_request(self) -> bool:
        if self.state != "open":
            return True
        if time.time() - self.opened_at >= self.recovery_timeout:
            self._set_state("half-open")
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        if self.state == "half-open":
            self.success_count += 1
            if self.success_count >= self.half_open_successes:
                self._set_state("closed")

    def record_failure(self) -> None:
        self.failure_count += 1
        self.success_count = 0
        if self.state == "half-open" or self.failure_count >= self.failure_threshold:
            self.opened_at = time.time()
            self._set_state("open")

    def _set_state(self, state: CircuitState) -> None:
        if self.state == state:
            return
        self.state = state
        if state != "half-open":
            self.success_count = 0
        if self.on_state_change:
            self.on_state_change(state)
