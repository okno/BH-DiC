"""Bounded browser queue, keyed locks, circuit breaking, and retry rules."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar, cast

from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicCircuitOpenError,
    DicReconciliationRequiredError,
    DicTransientError,
    DicUiChangedError,
)
from bh_dic.dic.models import ReconciliationResult, ReconciliationState

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class KeyedLockRegistry:
    """Stable per-target locks; queue workers may run unrelated reads concurrently."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def get(self, key: str) -> asyncio.Lock:
        async with self._registry_lock:
            return self._locks.setdefault(key, asyncio.Lock())


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or recovery_timeout_seconds <= 0:
            raise ValueError("invalid circuit breaker configuration")
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self) -> None:
        async with self._lock:
            if self._state is CircuitState.CLOSED:
                return
            if self._state is CircuitState.OPEN:
                opened_at = self._opened_at
                if opened_at is None:
                    raise RuntimeError("open circuit has no opening timestamp")
                if self._clock() - opened_at < self.recovery_timeout_seconds:
                    raise DicCircuitOpenError("DIC browser circuit is open")
                self._state = CircuitState.HALF_OPEN
            if self._half_open_in_flight:
                raise DicCircuitOpenError("DIC browser half-open probe is already running")
            self._half_open_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    async def record_failure(self) -> None:
        async with self._lock:
            self._half_open_in_flight = False
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()


@dataclass(frozen=True, slots=True)
class ReadRetryPolicy:
    attempts: int = 3
    initial_delay_seconds: float = 0.1
    maximum_delay_seconds: float = 1.0
    operation_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be positive")
        if self.initial_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if self.operation_timeout_seconds <= 0:
            raise ValueError("operation timeout must be positive")

    def delay(self, attempt: int) -> float:
        multiplier = float(2 ** max(0, attempt - 1))
        return min(self.maximum_delay_seconds, self.initial_delay_seconds * multiplier)


@dataclass(slots=True)
class _WorkItem[T]:
    key: str
    operation: Callable[[], Awaitable[T]]
    future: asyncio.Future[T]


class BrowserOperationQueue:
    """Backpressured async queue; it owns no browser and logs no operation payload."""

    def __init__(
        self, *, max_queue_size: int = 100, workers: int = 1, locks: KeyedLockRegistry | None = None
    ) -> None:
        if max_queue_size < 1 or workers < 1:
            raise ValueError("queue size and workers must be positive")
        self._queue: asyncio.Queue[_WorkItem[Any] | None] = asyncio.Queue(max_queue_size)
        self._worker_count = workers
        self._workers: list[asyncio.Task[None]] = []
        self._locks = locks or KeyedLockRegistry()
        self._start_lock = asyncio.Lock()
        self._closing = False

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._closing:
                raise RuntimeError("browser operation queue is closing")
            if not self._workers:
                self._workers = [
                    asyncio.create_task(self._worker(), name=f"dic-browser-worker-{index}")
                    for index in range(self._worker_count)
                ]

    async def submit(self, key: str, operation: Callable[[], Awaitable[T]]) -> T:
        if not key:
            raise ValueError("operation lock key cannot be empty")
        await self._ensure_started()
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        item = _WorkItem(key=key, operation=operation, future=future)
        await self._queue.put(cast(_WorkItem[Any], item))
        return await future

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            try:
                if item.future.cancelled():
                    continue
                lock = await self._locks.get(item.key)
                async with lock:
                    result = await item.operation()
                if not item.future.done():
                    item.future.set_result(result)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        async with self._start_lock:
            if self._closing:
                return
            self._closing = True
            workers = tuple(self._workers)
        if not workers:
            return
        await self._queue.join()
        for _ in workers:
            await self._queue.put(None)
        await asyncio.gather(*workers)
        self._workers.clear()


class BrowserCoordinator:
    """Read retries are bounded; writes are dispatched exactly once."""

    def __init__(
        self,
        *,
        queue: BrowserOperationQueue | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        read_retry: ReadRetryPolicy | None = None,
    ) -> None:
        self.queue = queue or BrowserOperationQueue()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.read_retry = read_retry or ReadRetryPolicy()

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return isinstance(exc, (DicTransientError, TimeoutError, ConnectionError))

    @staticmethod
    def _circuit_failure(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                DicAmbiguousWriteOutcomeError,
                DicTransientError,
                DicUiChangedError,
                TimeoutError,
                ConnectionError,
            ),
        )

    async def run_read(self, name: str, lock_key: str, operation: Callable[[], Awaitable[T]]) -> T:
        del name  # Kept for audit integration without exposing payloads here.

        async def execute() -> T:
            last_error: Exception | None = None
            for attempt in range(1, self.read_retry.attempts + 1):
                await self.circuit_breaker.before_call()
                try:
                    result = await asyncio.wait_for(
                        operation(), timeout=self.read_retry.operation_timeout_seconds
                    )
                except Exception as exc:
                    last_error = exc
                    if not self._retryable(exc):
                        if self._circuit_failure(exc):
                            await self.circuit_breaker.record_failure()
                        else:
                            await self.circuit_breaker.record_success()
                        raise
                    await self.circuit_breaker.record_failure()
                    if attempt == self.read_retry.attempts:
                        raise
                    await asyncio.sleep(self.read_retry.delay(attempt))
                else:
                    await self.circuit_breaker.record_success()
                    return result
            if last_error is None:
                raise DicTransientError("read retry loop ended without a result")
            raise last_error

        return await self.queue.submit(lock_key, execute)

    async def run_once(
        self,
        name: str,
        lock_key: str,
        operation: Callable[[], Awaitable[T]],
        *,
        timeout_seconds: float | None = None,
    ) -> T:
        """Run one serialized browser operation without automatic retry."""

        del name
        timeout = (
            self.read_retry.operation_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if timeout <= 0:
            raise ValueError("operation timeout must be positive")

        async def execute_once() -> T:
            await self.circuit_breaker.before_call()
            try:
                result = await asyncio.wait_for(operation(), timeout=timeout)
            except Exception as exc:
                if self._circuit_failure(exc):
                    await self.circuit_breaker.record_failure()
                else:
                    await self.circuit_breaker.record_success()
                raise
            await self.circuit_breaker.record_success()
            return result

        return await self.queue.submit(lock_key, execute_once)

    async def run_write(self, name: str, lock_key: str, operation: Callable[[], Awaitable[T]]) -> T:
        del name

        async def execute_once() -> T:
            await self.circuit_breaker.before_call()
            try:
                result = await asyncio.wait_for(
                    operation(), timeout=self.read_retry.operation_timeout_seconds
                )
            except Exception as exc:
                if self._circuit_failure(exc):
                    await self.circuit_breaker.record_failure()
                else:
                    await self.circuit_breaker.record_success()
                raise
            await self.circuit_breaker.record_success()
            return result

        return await self.queue.submit(lock_key, execute_once)

    async def reconcile_or_raise(
        self, reconciler: Callable[[], Awaitable[ReconciliationResult]]
    ) -> ReconciliationResult:
        outcome = await self.run_reconciliation("dic-browser", reconciler)
        if outcome.state is ReconciliationState.UNKNOWN:
            raise DicReconciliationRequiredError(outcome.detail)
        return outcome

    async def run_reconciliation(self, lock_key: str, operation: Callable[[], Awaitable[T]]) -> T:
        """Run one post-write read even if the normal browser circuit is open."""

        async def execute_once() -> T:
            try:
                result = await asyncio.wait_for(
                    operation(), timeout=self.read_retry.operation_timeout_seconds
                )
            except Exception:
                await self.circuit_breaker.record_failure()
                raise
            await self.circuit_breaker.record_success()
            return result

        return await self.queue.submit(lock_key, execute_once)

    async def close(self) -> None:
        await self.queue.close()
