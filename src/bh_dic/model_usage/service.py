"""Concurrency-safe lifecycle service for provider usage calls."""

from __future__ import annotations

import asyncio

from bh_dic.model_usage.models import (
    ModelUsageEvent,
    ModelUsageKey,
    ModelUsageStart,
    ModelUsageStatus,
    ModelUsageTotals,
)
from bh_dic.model_usage.repository import ModelUsageRepository
from bh_dic.openai.schemas import ProviderTokenUsage


class ModelUsageService:
    """Serialize one-way transitions in the supported single-node runtime."""

    def __init__(self, repository: ModelUsageRepository) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()

    async def start(self, record: ModelUsageStart) -> ModelUsageEvent:
        async with self._lock:
            return await self._repository.start(record)

    async def complete(
        self,
        key: ModelUsageKey,
        *,
        response_received: bool,
        usage: ProviderTokenUsage | None,
    ) -> ModelUsageEvent:
        """Persist an exact report, an unavailable report, or an unknown remote outcome."""

        if usage is not None and not response_received:
            raise ValueError("usage counters require a completed provider response")
        if usage is not None:
            status = ModelUsageStatus.REPORTED
        elif response_received:
            status = ModelUsageStatus.UNAVAILABLE
        else:
            status = ModelUsageStatus.UNKNOWN
        async with self._lock:
            return await self._repository.complete(key, status=status, usage=usage)

    async def totals(self, *, correlation_id: str | None = None) -> ModelUsageTotals:
        return await self._repository.totals(correlation_id=correlation_id)

    async def latest(self) -> ModelUsageEvent | None:
        return await self._repository.latest()


__all__ = ["ModelUsageService"]
