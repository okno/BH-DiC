"""Transactional append and verification service for the audit chain."""

from __future__ import annotations

import asyncio
from datetime import UTC

from sqlalchemy import func, select

from bh_dic.audit.chain import GENESIS_HASH, compute_event_hash, normalize_hmac_key, verify_events
from bh_dic.audit.models import (
    AuditEventInput,
    AuditEventMaterial,
    AuditEventView,
    AuditVerificationResult,
)
from bh_dic.database.engine import Database
from bh_dic.database.models import AuditChainState, AuditEvent
from bh_dic.errors import AuditAppendError, AuditIntegrityError
from bh_dic.logging import get_logger


class AuditService:
    """Append-only audit service.

    A process-local lock plus one database transaction serializes writers in the supported
    single-node deployment. PostgreSQL additionally honors ``FOR UPDATE`` on chain state.
    """

    def __init__(self, database: Database, hmac_key: bytes | str) -> None:
        self.database = database
        self._key = normalize_hmac_key(hmac_key)
        self._append_lock = asyncio.Lock()
        self._logger = get_logger("audit")

    async def append(self, event: AuditEventInput) -> AuditEventView:
        try:
            async with self._append_lock, self.database.transaction() as session:
                state_result = await session.execute(
                    select(AuditChainState).where(AuditChainState.id == 1).with_for_update()
                )
                state = state_result.scalar_one_or_none()
                if state is None:
                    state = AuditChainState(
                        id=1,
                        last_sequence=0,
                        last_hash=GENESIS_HASH,
                    )
                    session.add(state)
                    await session.flush()

                sequence = state.last_sequence + 1
                material = AuditEventMaterial(
                    sequence=sequence,
                    previous_hash=state.last_hash,
                    **event.model_dump(),
                )
                event_hash = compute_event_hash(self._key, material)
                row = AuditEvent(
                    sequence=sequence,
                    event_id=material.event_id,
                    timestamp_utc=material.timestamp_utc,
                    event_type=material.event_type,
                    correlation_id=material.correlation_id,
                    actor_discord_id=material.actor_discord_id,
                    guild_id=material.guild_id,
                    channel_id=material.channel_id,
                    function_id=material.function_id,
                    target_pseudonym=material.target_pseudonym,
                    outcome=material.outcome.value,
                    payload=dict(material.payload),
                    previous_hash=material.previous_hash,
                    event_hash=event_hash,
                )
                session.add(row)
                state.last_sequence = sequence
                state.last_hash = event_hash
                await session.flush()
                view = AuditEventView(**material.model_dump(), event_hash=event_hash)
        except Exception as exc:
            self._logger.exception(
                "Audit append failed",
                extra={
                    "event_type": "audit.append_failed",
                    "correlation_id": event.correlation_id,
                    "error_code": "AUDIT_APPEND_FAILED",
                    "outcome": "FAILED",
                },
            )
            raise AuditAppendError(context={"correlation_id": event.correlation_id}) from exc

        self._logger.info(
            "Audit event appended",
            extra={
                "event_type": "audit.event_appended",
                "correlation_id": view.correlation_id,
                "function_id": view.function_id,
                "outcome": view.outcome.value,
                "audit_sequence": view.sequence,
                "audit_event_id": view.event_id,
            },
        )
        return view

    async def verify(self) -> AuditVerificationResult:
        async with self.database.session() as session:
            rows_result = await session.execute(select(AuditEvent).order_by(AuditEvent.sequence))
            rows = rows_result.scalars().all()
            state = await session.get(AuditChainState, 1)

        events = tuple(self._view_from_row(row) for row in rows)
        if state is None:
            result = AuditVerificationResult(
                valid=False,
                event_count=len(events),
                last_sequence=0,
                last_hash=GENESIS_HASH,
                reason="audit chain state is missing",
            )
        else:
            result = verify_events(
                events,
                self._key,
                state_sequence=state.last_sequence,
                state_hash=state.last_hash,
            )
        self._logger.log(
            20 if result.valid else 40,
            "Audit chain verification completed",
            extra={
                "event_type": "audit.chain_verified" if result.valid else "audit.integrity_failed",
                "outcome": "SUCCESS" if result.valid else "FAILED",
                "error_code": None if result.valid else "AUDIT_INTEGRITY_FAILED",
                "audit_event_count": result.event_count,
                "failure_sequence": result.failure_sequence,
            },
        )
        return result

    async def verify_or_raise(self) -> AuditVerificationResult:
        result = await self.verify()
        if not result.valid:
            raise AuditIntegrityError(
                context={
                    "failure_sequence": result.failure_sequence,
                    "reason": result.reason,
                }
            )
        return result

    async def count(self) -> int:
        async with self.database.session() as session:
            result = await session.execute(select(func.count()).select_from(AuditEvent))
            return int(result.scalar_one())

    @staticmethod
    def _view_from_row(row: AuditEvent) -> AuditEventView:
        timestamp = row.timestamp_utc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return AuditEventView(
            sequence=row.sequence,
            event_id=row.event_id,
            timestamp_utc=timestamp,
            event_type=row.event_type,
            correlation_id=row.correlation_id,
            actor_discord_id=row.actor_discord_id,
            guild_id=row.guild_id,
            channel_id=row.channel_id,
            function_id=row.function_id,
            target_pseudonym=row.target_pseudonym,
            outcome=row.outcome,
            payload=row.payload,
            previous_hash=row.previous_hash,
            event_hash=row.event_hash,
        )
