"""Small async sliding-window limiter for single-node Discord handling."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class SlidingWindowRateLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("rate limit and window must be positive")
        self._limit = limit
        self._window = float(window_seconds)
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, *, cost: int = 1) -> RateLimitDecision:
        if not key or cost <= 0 or cost > self._limit:
            raise ValueError("invalid rate-limit key or cost")
        async with self._lock:
            now = self._clock()
            events = self._events[key]
            threshold = now - self._window
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) + cost > self._limit:
                retry_after = max(0.0, events[0] + self._window - now) if events else self._window
                return RateLimitDecision(False, max(0, self._limit - len(events)), retry_after)
            events.extend(now for _ in range(cost))
            return RateLimitDecision(True, self._limit - len(events), 0.0)

    async def clear(self, key: str) -> None:
        async with self._lock:
            self._events.pop(key, None)

    async def purge_idle(self) -> int:
        async with self._lock:
            now = self._clock()
            threshold = now - self._window
            removed = 0
            for key in tuple(self._events):
                events = self._events[key]
                while events and events[0] <= threshold:
                    events.popleft()
                if not events:
                    del self._events[key]
                    removed += 1
            return removed
