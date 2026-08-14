from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bh_dic.approvals import (
    ActionStatus,
    ApprovalService,
    AuthorizationError,
    ConfirmationHasher,
    DuplicateExecutionError,
    ExpiredActionError,
    InMemoryApprovalRepository,
    InvalidConfirmationError,
    InvalidStateError,
    StaleTargetError,
    WriteDisabledError,
)
from bh_dic.policies.roles import LogicalRole


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class MutableGate:
    def __init__(self) -> None:
        self.enabled = True

    def __call__(self) -> bool:
        return self.enabled


def _service() -> tuple[ApprovalService, InMemoryApprovalRepository, MutableClock, MutableGate]:
    repository = InMemoryApprovalRepository()
    clock = MutableClock()
    gate = MutableGate()
    service = ApprovalService(
        repository,
        ConfirmationHasher(b"confirmation-test-key-32-bytes!!"),
        writes_enabled=gate,
        clock=clock,
    )
    return service, repository, clock, gate


async def _prepare_update(service: ApprovalService):
    return await service.prepare(
        function_id="EMP-UPDATE-001",
        correlation_id="corr-1",
        requester_id="requester",
        guild_id="guild",
        channel_id="channel",
        target_employee_id="123",
        encrypted_parameters=b"ciphertext",
        redacted_diff={"job": {"before": "A", "after": "B"}},
        state_fingerprint="state-v1",
    )


@pytest.mark.asyncio
async def test_approval_confirmation_code_is_hashed_one_time_and_not_repr_leaked() -> None:
    service, _, _, _ = _service()
    prepared = await _prepare_update(service)
    assert prepared.confirmation_code not in repr(prepared)
    assert prepared.confirmation_code.encode() not in prepared.action.confirmation_digest
    with pytest.raises(InvalidConfirmationError):
        await service.confirm(
            prepared.action.action_id,
            requester_id="requester",
            confirmation_code="WRONGCODE",
        )
    confirmed = await service.confirm(
        prepared.action.action_id,
        requester_id="requester",
        confirmation_code=prepared.confirmation_code,
    )
    assert confirmed.status == ActionStatus.APPROVED
    with pytest.raises(InvalidConfirmationError):
        await service.confirm(
            prepared.action.action_id,
            requester_id="requester",
            confirmation_code=prepared.confirmation_code,
        )


@pytest.mark.asyncio
async def test_approval_ttl_expires_and_persists_expired_state() -> None:
    service, _, clock, _ = _service()
    prepared = await _prepare_update(service)
    clock.value += timedelta(minutes=11)
    with pytest.raises(ExpiredActionError):
        await service.confirm(
            prepared.action.action_id,
            requester_id="requester",
            confirmation_code=prepared.confirmation_code,
        )
    assert (await service.get(prepared.action.action_id)).status == ActionStatus.EXPIRED


@pytest.mark.asyncio
async def test_a2_requires_motivation_confirmation_and_two_independent_approvers() -> None:
    service, _, _, _ = _service()
    with pytest.raises(ValueError, match="motivation"):
        await service.prepare(
            function_id="EMP-BAL-002",
            correlation_id="corr",
            requester_id="requester",
            guild_id="guild",
            channel_id="channel",
            target_employee_id="123",
            encrypted_parameters=b"ciphertext",
            redacted_diff={},
            state_fingerprint="balance-v1",
        )
    prepared = await service.prepare(
        function_id="EMP-BAL-002",
        correlation_id="corr",
        requester_id="requester",
        guild_id="guild",
        channel_id="channel",
        target_employee_id="123",
        encrypted_parameters=b"ciphertext",
        redacted_diff={"balance": "[REDACTED]"},
        state_fingerprint="balance-v1",
        motivation="Correzione richiesta da HR",
    )
    with pytest.raises(InvalidConfirmationError):
        await service.confirm(
            prepared.action.action_id,
            requester_id="requester",
            confirmation_code=prepared.confirmation_code,
            text_confirmation="CONFIRM 999",
        )
    await service.confirm(
        prepared.action.action_id,
        requester_id="requester",
        confirmation_code=prepared.confirmation_code,
        text_confirmation="CONFIRM 123",
    )
    with pytest.raises(AuthorizationError):
        await service.approve(
            prepared.action.action_id,
            approver_id="requester",
            approver_roles=frozenset({LogicalRole.APPROVER}),
        )
    with pytest.raises(AuthorizationError):
        await service.approve(
            prepared.action.action_id,
            approver_id="unprivileged",
            approver_roles=frozenset({LogicalRole.READ_ONLY}),
        )
    partial = await service.approve(
        prepared.action.action_id,
        approver_id="approver-1",
        approver_roles=frozenset({LogicalRole.APPROVER}),
    )
    assert partial.status == ActionStatus.PARTIALLY_APPROVED
    with pytest.raises(AuthorizationError):
        await service.approve(
            prepared.action.action_id,
            approver_id="approver-1",
            approver_roles=frozenset({LogicalRole.APPROVER}),
        )
    approved = await service.approve(
        prepared.action.action_id,
        approver_id="approver-2",
        approver_roles=frozenset({LogicalRole.APPROVER}),
    )
    assert approved.status == ActionStatus.APPROVED
    assert approved.approved_by == {"approver-1", "approver-2"}
    with pytest.raises(InvalidStateError):
        await service.approve(
            prepared.action.action_id,
            approver_id="approver-3",
            approver_roles=frozenset({LogicalRole.APPROVER}),
        )


@pytest.mark.asyncio
async def test_kill_switch_is_rechecked_before_confirmation_and_execution() -> None:
    service, _, _, gate = _service()
    prepared = await _prepare_update(service)
    gate.enabled = False
    with pytest.raises(WriteDisabledError):
        await service.confirm(
            prepared.action.action_id,
            requester_id="requester",
            confirmation_code=prepared.confirmation_code,
        )
    gate.enabled = True
    await service.confirm(
        prepared.action.action_id,
        requester_id="requester",
        confirmation_code=prepared.confirmation_code,
    )
    gate.enabled = False
    with pytest.raises(WriteDisabledError):
        await service.begin_execution(
            prepared.action.action_id,
            current_state_fingerprint="state-v1",
        )


@pytest.mark.asyncio
async def test_approval_rejects_short_idempotency_key_and_unknown_approver_role() -> None:
    service, _, _, _ = _service()
    with pytest.raises(ValueError, match="idempotency"):
        await service.prepare(
            function_id="EMP-UPDATE-001",
            correlation_id="corr",
            requester_id="requester",
            guild_id="guild",
            channel_id="channel",
            target_employee_id="123",
            encrypted_parameters=b"ciphertext",
            redacted_diff={},
            state_fingerprint="state-v1",
            idempotency_key="short",
        )
    prepared = await service.prepare(
        function_id="EMP-CONTRACT-002",
        correlation_id="corr",
        requester_id="requester",
        guild_id="guild",
        channel_id="channel",
        target_employee_id="123",
        encrypted_parameters=b"ciphertext",
        redacted_diff={},
        state_fingerprint="state-v1",
    )
    await service.confirm(
        prepared.action.action_id,
        requester_id="requester",
        confirmation_code=prepared.confirmation_code,
    )
    with pytest.raises(AuthorizationError, match="unknown"):
        await service.approve(
            prepared.action.action_id,
            approver_id="approver",
            approver_roles=frozenset({"NOT_A_ROLE"}),
        )


@pytest.mark.asyncio
async def test_state_drift_blocks_execution_and_uncertain_write_is_never_retried() -> None:
    service, _, _, _ = _service()
    stale = await _prepare_update(service)
    await service.confirm(
        stale.action.action_id,
        requester_id="requester",
        confirmation_code=stale.confirmation_code,
    )
    with pytest.raises(StaleTargetError):
        await service.begin_execution(
            stale.action.action_id,
            current_state_fingerprint="state-v2",
        )
    assert (await service.get(stale.action.action_id)).status == ActionStatus.STALE

    uncertain = await _prepare_update(service)
    await service.confirm(
        uncertain.action.action_id,
        requester_id="requester",
        confirmation_code=uncertain.confirmation_code,
    )
    executing = await service.begin_execution(
        uncertain.action.action_id,
        current_state_fingerprint="state-v1",
    )
    assert executing.status == ActionStatus.EXECUTING
    unknown = await service.complete_failure(
        uncertain.action.action_id,
        result="browser disconnected",
        outcome_uncertain=True,
    )
    assert unknown.status == ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION
    with pytest.raises(DuplicateExecutionError):
        await service.begin_execution(
            uncertain.action.action_id,
            current_state_fingerprint="state-v1",
        )
    reconciled = await service.reconcile(
        uncertain.action.action_id,
        postcondition_met=False,
        result="read-back proves the change was not applied",
    )
    assert reconciled.status == ActionStatus.RECONCILED_NOT_APPLIED
