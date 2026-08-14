from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, update
from sqlalchemy.engine import URL

from bh_dic.audit.models import AuditEventInput, AuditOutcome
from bh_dic.audit.service import AuditService
from bh_dic.audit.verifier import AuditVerifier, verify_audit_chain
from bh_dic.database.engine import Database
from bh_dic.database.models import AuditChainState, AuditEvent
from bh_dic.errors import AuditAppendError, AuditIntegrityError


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


def _event(*, event_id: str, correlation_id: str) -> AuditEventInput:
    return AuditEventInput(
        event_id=event_id,
        timestamp_utc=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        event_type="employee.read.completed",
        correlation_id=correlation_id,
        function_id="EMP-READ-001",
        outcome=AuditOutcome.SUCCESS,
        payload={"result_count": 1},
    )


@pytest.mark.integration
async def test_audit_service_recreates_missing_state_and_verifier_wrappers(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "audit-state.sqlite3"))
    await database.create_schema()
    service = AuditService(database, b"A" * 32)
    try:
        async with database.transaction() as session:
            await session.execute(delete(AuditChainState))

        missing = await service.verify()
        assert missing.valid is False
        assert missing.reason == "audit chain state is missing"
        with pytest.raises(AuditIntegrityError):
            await service.verify_or_raise()

        appended = await service.append(
            _event(
                event_id="00000000-0000-4000-8000-000000000011",
                correlation_id="corr-audit-state-001",
            )
        )
        assert appended.sequence == 1
        assert await service.count() == 1

        verifier = AuditVerifier(database, b"A" * 32)
        assert (await verifier.verify()).valid is True
        assert (await verifier.verify_or_raise()).event_count == 1
        assert (await verify_audit_chain(database, b"A" * 32)).last_sequence == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_audit_append_wraps_database_error_and_chain_remains_valid(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "audit-error.sqlite3"))
    await database.create_schema()
    service = AuditService(database, b"B" * 32)
    duplicate = _event(
        event_id="00000000-0000-4000-8000-000000000021",
        correlation_id="corr-audit-error-001",
    )
    try:
        await service.append(duplicate)
        with pytest.raises(AuditAppendError) as captured:
            await service.append(duplicate.model_copy(update={"correlation_id": "corr-duplicate"}))

        assert captured.value.context == {"correlation_id": "corr-duplicate"}
        result = await service.verify_or_raise()
        assert result.event_count == 1
        assert result.last_sequence == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_verifier_raises_after_persisted_event_tampering(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "audit-tamper.sqlite3"))
    await database.create_schema()
    service = AuditService(database, b"C" * 32)
    try:
        await service.append(
            _event(
                event_id="00000000-0000-4000-8000-000000000031",
                correlation_id="corr-audit-tamper-001",
            )
        )
        async with database.transaction() as session:
            await session.execute(
                update(AuditEvent)
                .where(AuditEvent.sequence == 1)
                .values(payload={"result_count": 999})
            )

        verifier = AuditVerifier(database, b"C" * 32)
        with pytest.raises(AuditIntegrityError) as captured:
            await verifier.verify_or_raise()
        assert captured.value.context["failure_sequence"] == 1
        assert captured.value.context["reason"] == "event HMAC mismatch"
    finally:
        await database.dispose()


def test_audit_row_conversion_normalizes_naive_timestamp() -> None:
    row = AuditEvent(
        sequence=7,
        event_id="00000000-0000-4000-8000-000000000041",
        timestamp_utc=datetime(2026, 8, 14, 12, 0),
        event_type="synthetic.event",
        correlation_id="corr-row-conversion",
        actor_discord_id=None,
        guild_id=None,
        channel_id=None,
        function_id=None,
        target_pseudonym=None,
        outcome=AuditOutcome.SUCCESS.value,
        payload={},
        previous_hash="0" * 64,
        event_hash="1" * 64,
    )

    view = AuditService._view_from_row(row)
    assert view.timestamp_utc.tzinfo is UTC
    assert view.sequence == 7

    aware = AuditService._view_from_row(
        AuditEvent(
            sequence=8,
            event_id="00000000-0000-4000-8000-000000000042",
            timestamp_utc=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            event_type="synthetic.event",
            correlation_id="corr-row-aware",
            actor_discord_id=None,
            guild_id=None,
            channel_id=None,
            function_id=None,
            target_pseudonym=None,
            outcome=AuditOutcome.SUCCESS.value,
            payload={},
            previous_hash="1" * 64,
            event_hash="2" * 64,
        )
    )
    assert aware.timestamp_utc.tzinfo is UTC
