from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable, Deque, Generic, TypeVar

from app.infra.metrics import metrics


logger = logging.getLogger("app.circuit")

T = TypeVar("T")


class CircuitBreakerOpenError(RuntimeError):
    pass


class CircuitBreaker(Generic[T]):
    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int = 5,
        recovery_time: float = 30.0,
        window_seconds: float = 60.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_time = max(0.01, recovery_time)
        self.window_seconds = max(0.01, window_seconds)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._state: str = "closed"
        self._opened_at: float = 0.0
        self._failures: Deque[float] = deque()
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()
        metrics.record_circuit_state(self.name, self._state)

    async def call(self, fn: Callable[..., T | Awaitable[T]], *args, **kwargs) -> T:
        await self._ensure_available()
        try:
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            await self._record_failure()
            logger.warning("circuit_failure", extra={"extra": {"name": self.name, "state": self._state}})
            raise
        await self._record_success()
        return result  # type: ignore[return-value]

    async def _ensure_available(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._state == "open":
                if now - self._opened_at >= self.recovery_time:
                    self._state = "half_open"
                    self._half_open_calls = 0
                    metrics.record_circuit_state(self.name, self._state)
                else:
                    raise CircuitBreakerOpenError(f"circuit_open:{self.name}")
            if self._state == "half_open":
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(f"circuit_half_open_limit:{self.name}")
                self._half_open_calls += 1

    async def _record_failure(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            window_start = now - self.window_seconds
            while self._failures and self._failures[0] < window_start:
                self._failures.popleft()
            if len(self._failures) >= self.failure_threshold:
                self._state = "open"
                self._opened_at = now
                self._half_open_calls = 0
                metrics.record_circuit_state(self.name, self._state)
                logger.warning("circuit_opened", extra={"extra": {"name": self.name}})

    async def _record_success(self) -> None:
        async with self._lock:
            self._failures.clear()
            if self._state != "closed":
                logger.info("circuit_closed", extra={"extra": {"name": self.name}})
            self._state = "closed"
            self._half_open_calls = 0
            metrics.record_circuit_state(self.name, self._state)

    @property
    def state(self) -> str:
        return self._state
