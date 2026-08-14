"""Approval storage contracts and single-node implementations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bh_dic.approvals.models import ActionStatus, ApprovalRecord, PendingAction
from bh_dic.database.models import ActionExecution as ActionExecutionRow
from bh_dic.database.models import Approval as ApprovalRow
from bh_dic.database.models import PendingAction as PendingActionRow


class ApprovalRepository(Protocol):
    async def insert(self, action: PendingAction) -> None: ...

    async def get(self, action_id: str) -> PendingAction | None: ...

    async def replace(self, action: PendingAction, *, expected_version: int) -> None: ...

    async def claim_idempotency(self, key: str, action_id: str) -> bool: ...

    async def list_actions(self) -> tuple[PendingAction, ...]: ...


class InMemoryApprovalRepository:
    """Reference implementation; database adapters should provide atomic CAS."""

    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}
        self._idempotency: dict[str, str] = {}
        self._pending_idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def insert(self, action: PendingAction) -> None:
        async with self._lock:
            if action.action_id in self._actions:
                raise ValueError("duplicate action_id")
            if action.idempotency_key in self._pending_idempotency:
                raise ValueError("duplicate idempotency key")
            self._actions[action.action_id] = action
            self._pending_idempotency[action.idempotency_key] = action.action_id

    async def get(self, action_id: str) -> PendingAction | None:
        async with self._lock:
            return self._actions.get(action_id)

    async def replace(self, action: PendingAction, *, expected_version: int) -> None:
        async with self._lock:
            current = self._actions.get(action.action_id)
            if current is None:
                raise KeyError(action.action_id)
            if current.version != expected_version:
                raise RuntimeError("concurrent approval update")
            if action.version != expected_version + 1:
                raise ValueError("replacement must increment version exactly once")
            self._actions[action.action_id] = action

    async def claim_idempotency(self, key: str, action_id: str) -> bool:
        async with self._lock:
            if key in self._idempotency:
                return False
            self._idempotency[key] = action_id
            return True

    async def list_actions(self) -> tuple[PendingAction, ...]:
        async with self._lock:
            return tuple(self._actions.values())


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _encode_result(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"summary": value}


def _decode_result(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        summary = value.get("summary")
        if isinstance(summary, str):
            return summary
    if isinstance(value, str):
        return value
    raise ValueError("invalid persisted approval result")


class SqlAlchemyApprovalRepository:
    """Async SQLAlchemy implementation with optimistic CAS and durable claims."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def insert(self, action: PendingAction) -> None:
        row = PendingActionRow(
            action_id=action.action_id,
            correlation_id=action.correlation_id,
            function_id=action.function_id,
            requester_discord_id=action.requester_id,
            guild_id=action.guild_id,
            channel_id=action.channel_id,
            target_employee_id=action.target_employee_id,
            encrypted_parameters=action.encrypted_parameters,
            redacted_diff=dict(action.redacted_diff),
            motivation=action.motivation,
            state_fingerprint=action.state_fingerprint,
            status=action.status.value,
            created_at=action.created_at,
            expires_at=action.expires_at,
            approvals_required=action.approvals_required,
            approvals_received=len(action.approvals),
            confirmation_salt=action.confirmation_salt,
            confirmation_digest=action.confirmation_digest,
            confirmation_consumed_at=action.confirmation_consumed_at,
            idempotency_key=action.idempotency_key,
            execution_result=_encode_result(action.execution_result),
            postcondition_result=_encode_result(action.postcondition_result),
            rejection_reason=action.rejection_reason,
            version=action.version,
        )
        for approval in action.approvals:
            row.approvals.append(self._approval_row(action.action_id, approval))
        if action.rejected_by is not None:
            row.approvals.append(self._rejection_row(action))
        async with self._sessions() as session, session.begin():
            session.add(row)

    async def get(self, action_id: str) -> PendingAction | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(PendingActionRow)
                .options(selectinload(PendingActionRow.approvals))
                .where(PendingActionRow.action_id == action_id)
            )
            row = result.scalar_one_or_none()
            return None if row is None else self._to_domain(row)

    async def replace(self, action: PendingAction, *, expected_version: int) -> None:
        if action.version != expected_version + 1:
            raise ValueError("replacement must increment version exactly once")
        values = {
            "correlation_id": action.correlation_id,
            "function_id": action.function_id,
            "requester_discord_id": action.requester_id,
            "guild_id": action.guild_id,
            "channel_id": action.channel_id,
            "target_employee_id": action.target_employee_id,
            "encrypted_parameters": action.encrypted_parameters,
            "redacted_diff": dict(action.redacted_diff),
            "motivation": action.motivation,
            "state_fingerprint": action.state_fingerprint,
            "status": action.status.value,
            "created_at": action.created_at,
            "expires_at": action.expires_at,
            "approvals_required": action.approvals_required,
            "approvals_received": len(action.approvals),
            "confirmation_salt": action.confirmation_salt,
            "confirmation_digest": action.confirmation_digest,
            "confirmation_consumed_at": action.confirmation_consumed_at,
            "idempotency_key": action.idempotency_key,
            "execution_result": _encode_result(action.execution_result),
            "postcondition_result": _encode_result(action.postcondition_result),
            "rejection_reason": action.rejection_reason,
            "version": action.version,
        }
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(PendingActionRow)
                    .where(
                        PendingActionRow.action_id == action.action_id,
                        PendingActionRow.version == expected_version,
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("concurrent approval update")
            await self._sync_decisions(session, action)
            await self._sync_execution(session, action)

    async def claim_idempotency(self, key: str, action_id: str) -> bool:
        try:
            async with self._sessions() as session, session.begin():
                action = await session.scalar(
                    select(PendingActionRow).where(
                        PendingActionRow.action_id == action_id,
                        PendingActionRow.idempotency_key == key,
                    )
                )
                if action is None:
                    return False
                existing = await session.scalar(
                    select(ActionExecutionRow).where(
                        (ActionExecutionRow.action_id == action_id)
                        | (ActionExecutionRow.idempotency_key == key)
                    )
                )
                if existing is not None:
                    return False
                session.add(
                    ActionExecutionRow(
                        execution_id=str(uuid4()),
                        action_id=action_id,
                        idempotency_key=key,
                        status="CLAIMED",
                        started_at=datetime.now(UTC),
                        uncertain_outcome=False,
                    )
                )
            return True
        except IntegrityError:
            return False

    async def list_actions(self) -> tuple[PendingAction, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(PendingActionRow)
                .options(selectinload(PendingActionRow.approvals))
                .order_by(PendingActionRow.created_at, PendingActionRow.action_id)
            )
            return tuple(self._to_domain(row) for row in result.scalars())

    async def _sync_decisions(self, session: AsyncSession, action: PendingAction) -> None:
        result = await session.execute(
            select(ApprovalRow).where(ApprovalRow.action_id == action.action_id)
        )
        rows = tuple(result.scalars())
        existing = {(row.approver_discord_id, row.decision) for row in rows}
        for approval in action.approvals:
            key = (approval.approver_id, "APPROVED")
            if key not in existing:
                session.add(self._approval_row(action.action_id, approval))
        if action.rejected_by is not None:
            key = (action.rejected_by, "REJECTED")
            if key not in existing:
                session.add(self._rejection_row(action))

    @staticmethod
    async def _sync_execution(session: AsyncSession, action: PendingAction) -> None:
        execution_statuses = {
            ActionStatus.EXECUTING,
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION,
            ActionStatus.RECONCILED_NOT_APPLIED,
        }
        if action.status not in execution_statuses:
            return
        execution = await session.scalar(
            select(ActionExecutionRow).where(ActionExecutionRow.action_id == action.action_id)
        )
        if execution is None:
            raise RuntimeError("execution state exists without an idempotency claim")
        execution.status = (
            "STARTED" if action.status == ActionStatus.EXECUTING else action.status.value
        )
        execution.result = _encode_result(action.execution_result)
        execution.postcondition = _encode_result(action.postcondition_result)
        execution.uncertain_outcome = action.status == ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION
        execution.completed_at = (
            None if action.status == ActionStatus.EXECUTING else datetime.now(UTC)
        )

    @staticmethod
    def _approval_row(action_id: str, approval: ApprovalRecord) -> ApprovalRow:
        return ApprovalRow(
            approval_id=str(uuid4()),
            action_id=action_id,
            approver_discord_id=approval.approver_id,
            decision="APPROVED",
            created_at=approval.approved_at,
        )

    @staticmethod
    def _rejection_row(action: PendingAction) -> ApprovalRow:
        if action.rejected_by is None:
            raise ValueError("rejected action is missing its actor")
        return ApprovalRow(
            approval_id=str(uuid4()),
            action_id=action.action_id,
            approver_discord_id=action.rejected_by,
            decision="REJECTED",
            redacted_reason=action.rejection_reason,
            created_at=action.rejected_at or datetime.now(UTC),
        )

    @staticmethod
    def _to_domain(row: PendingActionRow) -> PendingAction:
        approvals = tuple(
            ApprovalRecord(
                approver_id=item.approver_discord_id,
                approved_at=_aware(item.created_at) or datetime.min.replace(tzinfo=UTC),
            )
            for item in sorted(row.approvals, key=lambda item: (item.created_at, item.approval_id))
            if item.decision == "APPROVED"
        )
        rejection = next((item for item in row.approvals if item.decision == "REJECTED"), None)
        created_at = _aware(row.created_at)
        expires_at = _aware(row.expires_at)
        if created_at is None or expires_at is None:
            raise ValueError("persisted approval timestamps cannot be null")
        return PendingAction(
            action_id=row.action_id,
            correlation_id=row.correlation_id,
            function_id=row.function_id,
            requester_id=row.requester_discord_id,
            guild_id=row.guild_id,
            channel_id=row.channel_id,
            target_employee_id=row.target_employee_id,
            encrypted_parameters=bytes(row.encrypted_parameters),
            redacted_diff=dict(row.redacted_diff),
            motivation=row.motivation,
            state_fingerprint=row.state_fingerprint,
            status=ActionStatus(row.status),
            created_at=created_at,
            expires_at=expires_at,
            approvals_required=row.approvals_required,
            approvals=approvals,
            confirmation_salt=bytes(row.confirmation_salt),
            confirmation_digest=bytes(row.confirmation_digest),
            confirmation_consumed_at=_aware(row.confirmation_consumed_at),
            idempotency_key=row.idempotency_key,
            execution_result=_decode_result(row.execution_result),
            postcondition_result=_decode_result(row.postcondition_result),
            rejection_reason=row.rejection_reason,
            rejected_by=None if rejection is None else rejection.approver_discord_id,
            rejected_at=None if rejection is None else _aware(rejection.created_at),
            version=row.version,
        )
