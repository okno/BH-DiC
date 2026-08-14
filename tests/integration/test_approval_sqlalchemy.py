from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.engine import URL

from bh_dic.approvals import (
    ActionStatus,
    ApprovalService,
    ConfirmationHasher,
    SqlAlchemyApprovalRepository,
)
from bh_dic.database.engine import Database
from bh_dic.database.models import ActionExecution, Approval, PendingAction
from bh_dic.policies.roles import LogicalRole
from bh_dic.security import PayloadCipher


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


@pytest.mark.integration
async def test_approval_sqlalchemy_round_trip_cas_and_durable_execution_claim(
    tmp_path: Path,
) -> None:
    database = Database(_sqlite_url(tmp_path / "approvals.sqlite3"))
    await database.create_schema()
    repository = SqlAlchemyApprovalRepository(database.sessions)
    cipher = PayloadCipher(b"p" * 32)
    service = ApprovalService(
        repository,
        ConfirmationHasher(b"c" * 32),
        writes_enabled=lambda: True,
    )
    encrypted = cipher.encrypt_json({"job": "Manager", "employee_id": "123"})
    try:
        prepared = await service.prepare(
            function_id="EMP-UPDATE-001",
            correlation_id="corr-sql-1",
            requester_id="requester",
            guild_id="guild",
            channel_id="channel",
            target_employee_id="123",
            encrypted_parameters=encrypted,
            redacted_diff={"job": {"before": "A", "after": "B"}},
            state_fingerprint="state-v1",
        )
        original = prepared.action
        confirmed = await service.confirm(
            original.action_id,
            requester_id="requester",
            confirmation_code=prepared.confirmation_code,
        )
        assert confirmed.status == ActionStatus.APPROVED

        fresh_repository = SqlAlchemyApprovalRepository(database.sessions)
        restored = await fresh_repository.get(original.action_id)
        assert restored == confirmed
        assert cipher.decrypt_json(restored.encrypted_parameters) == {
            "employee_id": "123",
            "job": "Manager",
        }

        stale_candidate = replace(original, status=ActionStatus.APPROVED, version=2)
        with pytest.raises(RuntimeError, match="concurrent"):
            await fresh_repository.replace(stale_candidate, expected_version=1)

        executing = await service.begin_execution(
            original.action_id,
            current_state_fingerprint="state-v1",
        )
        assert executing.status == ActionStatus.EXECUTING
        completed = await service.complete_success(
            original.action_id,
            execution_result="adapter completed",
            postcondition_result="read-back matched",
            postcondition_verified=True,
        )
        assert completed.status == ActionStatus.SUCCEEDED
        assert not await fresh_repository.claim_idempotency(
            completed.idempotency_key, completed.action_id
        )

        persisted = await fresh_repository.get(original.action_id)
        assert persisted == completed
        assert (await fresh_repository.list_actions()) == (completed,)
        async with database.sessions() as session:
            action_row = await session.get(PendingAction, original.action_id)
            execution_row = await session.scalar(
                select(ActionExecution).where(ActionExecution.action_id == original.action_id)
            )
            assert action_row is not None
            assert b"Manager" not in action_row.encrypted_parameters
            assert execution_row is not None
            assert execution_row.status == ActionStatus.SUCCEEDED.value
            assert execution_row.completed_at is not None
            assert execution_row.uncertain_outcome is False
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_approval_sqlalchemy_persists_a2_and_rejection_decisions(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "approval-decisions.sqlite3"))
    await database.create_schema()
    repository = SqlAlchemyApprovalRepository(database.sessions)
    service = ApprovalService(
        repository,
        ConfirmationHasher(b"c" * 32),
        writes_enabled=lambda: True,
    )
    try:
        cipher = PayloadCipher(b"p" * 32)
        critical = await service.prepare(
            function_id="EMP-BAL-002",
            correlation_id="corr-a2",
            requester_id="requester",
            guild_id="guild",
            channel_id="channel",
            target_employee_id="123",
            encrypted_parameters=cipher.encrypt_json({"balance": 10}),
            redacted_diff={"balance": "[REDACTED]"},
            state_fingerprint="balance-v1",
            motivation="Correzione autorizzata",
        )
        await service.confirm(
            critical.action.action_id,
            requester_id="requester",
            confirmation_code=critical.confirmation_code,
            text_confirmation="CONFIRM 123",
        )
        await service.approve(
            critical.action.action_id,
            approver_id="approver-1",
            approver_roles=frozenset({LogicalRole.APPROVER}),
        )
        approved = await service.approve(
            critical.action.action_id,
            approver_id="approver-2",
            approver_roles=frozenset({LogicalRole.APPROVER}),
        )
        restored = await repository.get(approved.action_id)
        assert restored == approved

        rejectable = await service.prepare(
            function_id="EMP-CONTRACT-002",
            correlation_id="corr-reject",
            requester_id="requester",
            guild_id="guild",
            channel_id="channel",
            target_employee_id="123",
            encrypted_parameters=cipher.encrypt_json({"contract": "change"}),
            redacted_diff={},
            state_fingerprint="contract-v1",
        )
        await service.confirm(
            rejectable.action.action_id,
            requester_id="requester",
            confirmation_code=rejectable.confirmation_code,
        )
        rejected = await service.reject(
            rejectable.action.action_id,
            actor_id="hr-approver",
            actor_roles=frozenset({LogicalRole.HR_WRITE}),
            reason="Dati non corretti",
        )
        restored_rejection = await repository.get(rejected.action_id)
        assert restored_rejection == rejected
        assert restored_rejection.rejected_by == "hr-approver"
        assert restored_rejection.rejected_at is not None

        async with database.sessions() as session:
            decisions = tuple(
                await session.scalars(
                    select(Approval).order_by(Approval.action_id, Approval.created_at)
                )
            )
        assert [item.decision for item in decisions].count("APPROVED") == 2
        assert [item.decision for item in decisions].count("REJECTED") == 1
    finally:
        await database.dispose()
