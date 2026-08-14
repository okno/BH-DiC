from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text, update
from sqlalchemy.engine import URL

from bh_dic.audit.models import AuditEventInput, AuditOutcome
from bh_dic.audit.service import AuditService
from bh_dic.database.engine import Database
from bh_dic.database.migrations import run_migrations_async
from bh_dic.database.models import AuditChainState, AuditEvent, Base

EXPECTED_TABLES = {
    "action_executions",
    "approvals",
    "audit_chain_state",
    "audit_events",
    "browser_jobs",
    "discord_requests",
    "feature_flags",
    "pending_actions",
    "schema_versions",
    "uploaded_files",
}

PENDING_ACTION_COLUMNS = {
    "action_id",
    "correlation_id",
    "function_id",
    "requester_discord_id",
    "guild_id",
    "channel_id",
    "target_employee_id",
    "encrypted_parameters",
    "redacted_diff",
    "motivation",
    "state_fingerprint",
    "status",
    "created_at",
    "expires_at",
    "approvals_required",
    "approvals_received",
    "confirmation_salt",
    "confirmation_digest",
    "confirmation_consumed_at",
    "idempotency_key",
    "execution_result",
    "postcondition_result",
    "rejection_reason",
    "version",
}


def sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


@pytest.mark.integration
async def test_database_creates_minimal_schema_with_sqlite_wal(tmp_path: Path) -> None:
    database = Database(sqlite_url(tmp_path / "foundation.sqlite3"))
    try:
        await database.create_schema()
        health = await database.healthcheck()
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
            pending_columns = await connection.run_sync(
                lambda sync: {
                    column["name"]: column
                    for column in inspect(sync).get_columns("pending_actions")
                }
            )
        async with database.session() as session:
            audit_state = await session.get(AuditChainState, 1)

        assert EXPECTED_TABLES <= tables
        assert set(pending_columns) == PENDING_ACTION_COLUMNS
        assert pending_columns["target_employee_id"]["nullable"] is True
        assert pending_columns["version"]["default"] is not None
        assert audit_state is not None
        assert audit_state.last_sequence == 0
        assert audit_state.last_hash == "0" * 64
        assert health == {"status": "ok", "journal_mode": "wal"}
        assert foreign_keys == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_initial_alembic_migration_is_runnable(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "migrated.sqlite3")
    await run_migrations_async(url)
    database = Database(url)
    try:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            schema_differences = await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )
        assert EXPECTED_TABLES <= tables
        assert "alembic_version" in tables
        assert schema_differences == []
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_audit_service_serializes_writers_and_detects_database_tampering(
    tmp_path: Path,
) -> None:
    database = Database(sqlite_url(tmp_path / "audit.sqlite3"))
    await database.create_schema()
    service = AuditService(database, b"k" * 32)

    async def append(index: int) -> None:
        await service.append(
            AuditEventInput(
                event_type="employee.read.completed",
                correlation_id=f"corr-test-{index:04d}",
                function_id="EMP-READ-001",
                outcome=AuditOutcome.SUCCESS,
                payload={"result_count": index},
            )
        )

    try:
        await asyncio.gather(*(append(index) for index in range(1, 11)))
        valid = await service.verify()
        assert valid.valid is True
        assert valid.event_count == 10
        assert valid.last_sequence == 10

        async with database.transaction() as session:
            await session.execute(
                update(AuditEvent)
                .where(AuditEvent.sequence == 4)
                .values(outcome=AuditOutcome.FAILED.value)
            )

        invalid = await service.verify()
        assert invalid.valid is False
        assert invalid.failure_sequence == 4
        assert invalid.reason == "event HMAC mismatch"
    finally:
        await database.dispose()
