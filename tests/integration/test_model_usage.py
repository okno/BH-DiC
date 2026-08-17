from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import URL

from bh_dic.database.engine import Database
from bh_dic.database.migrations import run_migrations_async
from bh_dic.database.models import ModelUsageEvent as ModelUsageEventRow
from bh_dic.model_usage.models import (
    ModelUsageEvent,
    ModelUsageKey,
    ModelUsageStart,
    ModelUsageStatus,
)
from bh_dic.model_usage.repository import (
    ModelUsageConflictError,
    SqlAlchemyModelUsageRepository,
)
from bh_dic.model_usage.service import ModelUsageService
from bh_dic.openai.schemas import ProviderTokenUsage


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


def _key(suffix: str = "0001", *, ordinal: int = 1) -> ModelUsageKey:
    return ModelUsageKey(
        correlation_id=f"corr-model-usage-{suffix}",
        purpose="intent_routing",
        ordinal=ordinal,
    )


def _start(key: ModelUsageKey, *, provider: str = "groq") -> ModelUsageStart:
    return ModelUsageStart(
        key=key,
        provider=provider,
        model="openai/gpt-oss-120b",
    )


@pytest.mark.integration
async def test_model_usage_is_idempotent_persistent_and_aggregated_after_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-usage.sqlite3"
    database = Database(_sqlite_url(path))
    await database.create_schema()
    service = ModelUsageService(SqlAlchemyModelUsageRepository(database.sessions))
    key = _key()
    usage = ProviderTokenUsage(input_tokens=120, output_tokens=30, total_tokens=150)
    try:
        started = await service.start(_start(key))
        duplicate_start = await service.start(_start(key))
        assert duplicate_start.usage_id == started.usage_id
        assert duplicate_start.status is ModelUsageStatus.STARTED

        reported = await service.complete(key, response_received=True, usage=usage)
        duplicate_report = await service.complete(key, response_received=True, usage=usage)
        assert duplicate_report == reported
        assert reported.status is ModelUsageStatus.REPORTED
        assert reported.usage == usage

        async with database.session() as session:
            count = await session.scalar(select(func.count()).select_from(ModelUsageEventRow))
        assert count == 1
    finally:
        await database.dispose()

    reopened = Database(_sqlite_url(path))
    reopened_service = ModelUsageService(SqlAlchemyModelUsageRepository(reopened.sessions))
    try:
        totals = await reopened_service.totals()
        per_request = await reopened_service.totals(correlation_id=key.correlation_id)
        assert totals == per_request
        assert totals.total_calls == 1
        assert totals.reported_calls == 1
        assert totals.started_calls == 0
        assert totals.usage == usage
        assert totals.first_recorded_at is not None
        assert totals.last_completed_at is not None
        latest = await reopened_service.latest()
        assert latest is not None
        assert latest.status is ModelUsageStatus.REPORTED
    finally:
        await reopened.dispose()


@pytest.mark.integration
async def test_model_usage_concurrency_and_terminal_gaps_remain_explicit(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "model-usage-concurrent.sqlite3"))
    await database.create_schema()
    service = ModelUsageService(SqlAlchemyModelUsageRepository(database.sessions))
    same_key = _key("same")
    try:
        starts = await asyncio.gather(*(service.start(_start(same_key)) for _ in range(12)))
        assert len({event.usage_id for event in starts}) == 1

        usage = ProviderTokenUsage(input_tokens=10, output_tokens=4, total_tokens=14)
        completions = await asyncio.gather(
            *(service.complete(same_key, response_received=True, usage=usage) for _ in range(12))
        )
        assert {event.status for event in completions} == {ModelUsageStatus.REPORTED}

        unavailable_key = _key("unavailable")
        unknown_key = _key("unknown")
        started_key = _key("started")
        await service.start(_start(unavailable_key))
        await service.start(_start(unknown_key, provider="openai"))
        await service.start(_start(started_key, provider="llama"))
        unavailable = await service.complete(
            unavailable_key,
            response_received=True,
            usage=None,
        )
        unknown = await service.complete(
            unknown_key,
            response_received=False,
            usage=None,
        )
        assert unavailable.status is ModelUsageStatus.UNAVAILABLE
        assert unknown.status is ModelUsageStatus.UNKNOWN

        totals = await service.totals()
        assert totals.total_calls == 4
        assert totals.reported_calls == 1
        assert totals.unavailable_calls == 1
        assert totals.unknown_calls == 1
        assert totals.started_calls == 1
        assert totals.usage == usage

        latest_key = _key("latest-unknown")
        await service.start(_start(latest_key))
        await service.complete(latest_key, response_received=False, usage=None)
        latest = await service.latest()
        assert latest is not None
        assert latest.key == latest_key
        assert latest.status is ModelUsageStatus.UNKNOWN
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_model_usage_rejects_key_rebinding_and_terminal_rewrites(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "model-usage-conflict.sqlite3"))
    await database.create_schema()
    service = ModelUsageService(SqlAlchemyModelUsageRepository(database.sessions))
    key = _key("conflict")
    try:
        await service.start(_start(key, provider="groq"))
        with pytest.raises(ModelUsageConflictError, match="metadata"):
            await service.start(_start(key, provider="openai"))

        await service.complete(key, response_received=True, usage=None)
        with pytest.raises(ModelUsageConflictError, match="terminal"):
            await service.complete(
                key,
                response_received=True,
                usage=ProviderTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )
        with pytest.raises(ValueError, match="completed provider response"):
            await service.complete(
                _key("invalid"),
                response_received=False,
                usage=ProviderTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_model_usage_schema_contains_only_minimized_operational_metadata(
    tmp_path: Path,
) -> None:
    database = Database(_sqlite_url(tmp_path / "model-usage-schema.sqlite3"))
    await database.create_schema()
    try:
        async with database.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"] for column in inspect(sync).get_columns("model_usage_events")
                }
            )
        assert columns == {
            "usage_id",
            "correlation_id",
            "purpose",
            "ordinal",
            "provider",
            "model",
            "status",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "created_at",
            "completed_at",
        }
        forbidden = {
            "prompt",
            "request",
            "response",
            "user_id",
            "guild_id",
            "channel_id",
            "employee_id",
            "request_id",
            "provider_body",
        }
        assert columns.isdisjoint(forbidden)
    finally:
        await database.dispose()


def test_model_usage_domain_schema_is_strict_and_lifecycle_consistent() -> None:
    key = _key("schema")
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ModelUsageKey(correlation_id=key.correlation_id, purpose="Intent Routing")
    with pytest.raises(ValidationError):
        ModelUsageStart(key=key, provider="groq", model="secret model")
    with pytest.raises(ValidationError):
        ModelUsageStart(key=key, provider="groq", model="gsk_not-a-model")
    with pytest.raises(ValidationError):
        ModelUsageEvent(
            key=key,
            provider="groq",
            model="openai/gpt-oss-120b",
            status=ModelUsageStatus.REPORTED,
            created_at=now,
            completed_at=now,
            usage=None,
        )
    with pytest.raises(ValidationError):
        ModelUsageEvent(
            key=key,
            provider="groq",
            model="openai/gpt-oss-120b",
            status=ModelUsageStatus.UNKNOWN,
            created_at=now,
            completed_at=now,
            usage=ProviderTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


@pytest.mark.integration
async def test_model_usage_migration_upgrades_an_existing_foundation_database(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "model-usage-migration.sqlite3")
    await run_migrations_async(url, "0001_foundation")
    await run_migrations_async(url)
    database = Database(url)
    try:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"] for column in inspect(sync).get_columns("model_usage_events")
                }
            )
        assert "model_usage_events" in tables
        assert {"correlation_id", "purpose", "ordinal", "status", "total_tokens"} <= columns
    finally:
        await database.dispose()
