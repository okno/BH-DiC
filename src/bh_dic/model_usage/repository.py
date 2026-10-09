"""SQLAlchemy persistence for privacy-minimized model usage telemetry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import Select, case, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bh_dic.database.models import ModelUsageEvent as ModelUsageEventRow
from bh_dic.model_usage.models import (
    ZERO_TOKEN_USAGE,
    ModelUsageEvent,
    ModelUsageKey,
    ModelUsageStart,
    ModelUsageStatus,
    ModelUsageTotals,
)
from bh_dic.openai.schemas import ProviderTokenUsage


class ModelUsageConflictError(RuntimeError):
    """A logical call key was reused for incompatible immutable metadata or state."""


class ModelUsageRepository(Protocol):
    async def start(self, record: ModelUsageStart) -> ModelUsageEvent: ...

    async def get(self, key: ModelUsageKey) -> ModelUsageEvent | None: ...

    async def latest(self) -> ModelUsageEvent | None: ...

    async def complete(
        self,
        key: ModelUsageKey,
        *,
        status: ModelUsageStatus,
        usage: ProviderTokenUsage | None,
    ) -> ModelUsageEvent: ...

    async def totals(self, *, correlation_id: str | None = None) -> ModelUsageTotals: ...


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAlchemyModelUsageRepository:
    """Retained rows with an idempotent key and one-way, terminal completion."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def start(self, record: ModelUsageStart) -> ModelUsageEvent:
        row = ModelUsageEventRow(
            correlation_id=record.key.correlation_id,
            purpose=record.key.purpose,
            ordinal=record.key.ordinal,
            provider=record.provider,
            model=record.model,
            status=ModelUsageStatus.STARTED.value,
            created_at=datetime.now(UTC),
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await self.get(record.key)
            if existing is None:
                raise ModelUsageConflictError("model usage start conflicted") from None
            if existing.provider != record.provider or existing.model != record.model:
                raise ModelUsageConflictError("model usage key metadata conflicted") from None
            return existing
        return self._to_domain(row)

    async def get(self, key: ModelUsageKey) -> ModelUsageEvent | None:
        async with self._sessions() as session:
            row = await session.scalar(self._by_key(key))
            return None if row is None else self._to_domain(row)

    async def latest(self) -> ModelUsageEvent | None:
        statement = (
            select(ModelUsageEventRow)
            .order_by(
                ModelUsageEventRow.created_at.desc(),
                ModelUsageEventRow.completed_at.desc(),
                ModelUsageEventRow.usage_id.desc(),
            )
            .limit(1)
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
            return None if row is None else self._to_domain(row)

    async def complete(
        self,
        key: ModelUsageKey,
        *,
        status: ModelUsageStatus,
        usage: ProviderTokenUsage | None,
    ) -> ModelUsageEvent:
        if status is ModelUsageStatus.STARTED:
            raise ValueError("completion status must be terminal")
        if (status is ModelUsageStatus.REPORTED) != (usage is not None):
            raise ValueError("only REPORTED completion accepts exact counters")

        completed_at = datetime.now(UTC)
        values: dict[str, object | None] = {
            "status": status.value,
            "completed_at": completed_at,
            "input_tokens": None if usage is None else usage.input_tokens,
            "output_tokens": None if usage is None else usage.output_tokens,
            "total_tokens": None if usage is None else usage.total_tokens,
        }
        statement = (
            update(ModelUsageEventRow)
            .where(
                ModelUsageEventRow.correlation_id == key.correlation_id,
                ModelUsageEventRow.purpose == key.purpose,
                ModelUsageEventRow.ordinal == key.ordinal,
                ModelUsageEventRow.status == ModelUsageStatus.STARTED.value,
            )
            .values(**values)
        )
        async with self._sessions() as session, session.begin():
            result = cast(CursorResult[object], await session.execute(statement))
            changed = result.rowcount == 1

        current = await self.get(key)
        if current is None:
            raise KeyError("model usage call is not started")
        if changed:
            return current
        if current.status is status and current.usage == usage:
            return current
        raise ModelUsageConflictError("model usage call is already terminal")

    async def totals(self, *, correlation_id: str | None = None) -> ModelUsageTotals:
        status = ModelUsageEventRow.status
        statement = select(
            func.count(ModelUsageEventRow.usage_id),
            func.sum(case((status == ModelUsageStatus.STARTED.value, 1), else_=0)),
            func.sum(case((status == ModelUsageStatus.REPORTED.value, 1), else_=0)),
            func.sum(case((status == ModelUsageStatus.UNAVAILABLE.value, 1), else_=0)),
            func.sum(case((status == ModelUsageStatus.UNKNOWN.value, 1), else_=0)),
            func.sum(
                case(
                    (status == ModelUsageStatus.REPORTED.value, ModelUsageEventRow.input_tokens),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (status == ModelUsageStatus.REPORTED.value, ModelUsageEventRow.output_tokens),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (status == ModelUsageStatus.REPORTED.value, ModelUsageEventRow.total_tokens),
                    else_=0,
                )
            ),
            func.min(ModelUsageEventRow.created_at),
            func.max(ModelUsageEventRow.completed_at),
        )
        if correlation_id is not None:
            statement = statement.where(ModelUsageEventRow.correlation_id == correlation_id)
        async with self._sessions() as session:
            row = (await session.execute(statement)).one()

        total_calls = int(row[0] or 0)
        return ModelUsageTotals(
            total_calls=total_calls,
            started_calls=int(row[1] or 0),
            reported_calls=int(row[2] or 0),
            unavailable_calls=int(row[3] or 0),
            unknown_calls=int(row[4] or 0),
            usage=(
                ZERO_TOKEN_USAGE
                if total_calls == 0
                else ProviderTokenUsage(
                    input_tokens=int(row[5] or 0),
                    output_tokens=int(row[6] or 0),
                    total_tokens=int(row[7] or 0),
                )
            ),
            first_recorded_at=_aware(row[8]),
            last_completed_at=_aware(row[9]),
        )

    @staticmethod
    def _by_key(key: ModelUsageKey) -> Select[tuple[ModelUsageEventRow]]:
        return select(ModelUsageEventRow).where(
            ModelUsageEventRow.correlation_id == key.correlation_id,
            ModelUsageEventRow.purpose == key.purpose,
            ModelUsageEventRow.ordinal == key.ordinal,
        )

    @staticmethod
    def _to_domain(row: ModelUsageEventRow) -> ModelUsageEvent:
        usage: ProviderTokenUsage | None = None
        if row.status == ModelUsageStatus.REPORTED.value:
            if row.input_tokens is None or row.output_tokens is None or row.total_tokens is None:
                raise ValueError("reported model usage counters are incomplete")
            usage = ProviderTokenUsage(
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                total_tokens=row.total_tokens,
            )
        created_at = _aware(row.created_at)
        if created_at is None:
            raise ValueError("model usage created_at is missing")
        return ModelUsageEvent(
            usage_id=row.usage_id,
            key=ModelUsageKey(
                correlation_id=row.correlation_id,
                purpose=row.purpose,
                ordinal=row.ordinal,
            ),
            provider=row.provider,
            model=row.model,
            status=ModelUsageStatus(row.status),
            usage=usage,
            created_at=created_at,
            completed_at=_aware(row.completed_at),
        )


__all__ = [
    "ModelUsageConflictError",
    "ModelUsageRepository",
    "SqlAlchemyModelUsageRepository",
]
