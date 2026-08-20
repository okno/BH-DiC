from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.dic.errors import DicApprovalError, DicValidationError, DicWriteDisabledError
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import PayrollMetadata
from bh_dic.policies.feature_flags import RuntimeFeatureFlags
from bh_dic.services.dic_service import DicService


def pending_action(*, enabled_status: ActionStatus = ActionStatus.EXECUTING) -> PendingAction:
    now = datetime.now(UTC)
    return PendingAction(
        action_id=str(uuid4()),
        correlation_id="corr-00000001",
        function_id="EMP-UPDATE-001",
        requester_id="requester-1",
        guild_id="guild-1",
        channel_id="channel-1",
        target_employee_id="EMP-SYNTH-001",
        encrypted_parameters=b"encrypted",
        redacted_diff={"job_title": {"before": "[REDACTED]", "after": "QA lead"}},
        motivation=None,
        state_fingerprint="state-00000001",
        status=enabled_status,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        approvals_required=0,
        approvals=(),
        confirmation_salt=b"salt",
        confirmation_digest=b"digest",
        confirmation_consumed_at=now,
        idempotency_key="idem-00000001",
    )


def enabled_flags() -> RuntimeFeatureFlags:
    return RuntimeFeatureFlags(
        baseline={
            "ENABLE_WRITE_ACTIONS": True,
            "ENABLE_EMPLOYEE_UPDATE": True,
        }
    )


def test_service_preserves_approval_uuid_and_builds_redacted_preview() -> None:
    pending = pending_action()
    service = DicService(MockDicAdapter(), enabled_flags())

    prepared = service.prepare_execution(pending, {"job_title": "QA lead"})

    assert prepared.action_id == pending.action_id
    assert prepared.function_id.value == pending.function_id
    assert prepared.preview[0].after_redacted == "QA lead"
    assert len(prepared.request_fingerprint) == 64


def test_service_kill_switch_is_rechecked_before_execution() -> None:
    service = DicService(MockDicAdapter(), RuntimeFeatureFlags())
    with pytest.raises(DicWriteDisabledError):
        service.prepare_execution(pending_action(), {"job_title": "QA lead"})


def test_service_requires_atomic_execution_claim_and_consumed_confirmation() -> None:
    service = DicService(MockDicAdapter(), enabled_flags())
    with pytest.raises(DicApprovalError):
        service.prepare_execution(
            pending_action(enabled_status=ActionStatus.APPROVED), {"job_title": "QA lead"}
        )


def test_service_refuses_credential_like_parameters() -> None:
    service = DicService(MockDicAdapter(), enabled_flags())
    with pytest.raises(DicValidationError, match="credential-like"):
        service.prepare_execution(pending_action(), {"password": "must-not-cross-boundary"})


@pytest.mark.parametrize(
    "parameters",
    [
        {"job_title": "QA lead", "roles": ["Admin"]},
        {},
    ],
)
def test_service_reapplies_closed_catalog_to_internal_write_entry_points(
    parameters: dict[str, object],
) -> None:
    service = DicService(MockDicAdapter(), enabled_flags())

    with pytest.raises(DicValidationError, match="policy catalog"):
        service.prepare_execution(pending_action(), parameters)


def test_service_accepts_only_server_derived_document_execution_capability() -> None:
    flags = RuntimeFeatureFlags(
        baseline={
            "ENABLE_WRITE_ACTIONS": True,
            "ENABLE_DOCUMENT_UPLOAD": True,
        }
    )
    service = DicService(MockDicAdapter(), flags, capabilities=frozenset({"clamav"}))
    pending = replace(
        pending_action(),
        function_id="EMP-DOC-002",
        redacted_diff={"category": {"before": "[NOT_SET]", "after": "CV"}},
    )
    execution_parameters: dict[str, object] = {
        "category": "CV",
        "safe_local_path": "C:/synthetic/claimed-upload",
        "safe_local_sha256": "a" * 64,
        "safe_local_size": 4,
        "detected_mime": "application/pdf",
    }

    prepared = service.prepare_execution(pending, execution_parameters)

    assert prepared.parameters == execution_parameters
    assert "upload_id" not in prepared.parameters

    for invalid in (
        {**execution_parameters, "title": "must-not-be-invented"},
        {**execution_parameters, "upload_id": "0" * 32},
        {key: value for key, value in execution_parameters.items() if key != "detected_mime"},
        {**execution_parameters, "safe_local_sha256": "not-a-digest"},
    ):
        with pytest.raises(DicValidationError, match="document execution"):
            service.prepare_execution(pending, invalid)


@pytest.mark.asyncio
async def test_service_executes_approved_mock_write_deterministically() -> None:
    adapter = MockDicAdapter()
    service = DicService(adapter, enabled_flags())
    pending = pending_action()

    first = await service.execute(pending, {"job_title": "QA lead"})
    second = await service.execute(pending, {"job_title": "QA lead"})

    assert first == second
    assert (await adapter.get_employee_summary("EMP-SYNTH-001")).job_title == "QA lead"


def test_service_refuses_expired_execution_claim() -> None:
    pending = pending_action()
    service = DicService(
        MockDicAdapter(), enabled_flags(), clock=lambda: pending.expires_at + timedelta(seconds=1)
    )
    with pytest.raises(DicApprovalError, match="expired"):
        service.prepare_execution(pending, {"job_title": "QA lead"})


@pytest.mark.asyncio
async def test_reconciliation_remains_available_after_kill_switch() -> None:
    pending = pending_action(enabled_status=ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION)
    service = DicService(MockDicAdapter(), RuntimeFeatureFlags())
    result = await service.reconcile(pending, {"job_title": "QA lead"})
    assert result.state.value == "confirmed_not_applied"


@pytest.mark.asyncio
async def test_service_compares_registered_payroll_pages_for_the_complete_employee_list() -> None:
    adapter = MockDicAdapter()
    adapter._payrolls["EMP-SYNTH-001"].append(
        PayrollMetadata(
            payroll_id="PAY-SYNTH-007",
            employee_id="EMP-SYNTH-001",
            year=2026,
            month=7,
            status="published",
            published_at="2026-07-31",
        )
    )
    service = DicService(adapter, RuntimeFeatureFlags())

    result = await service.find_employees_with_payroll(year=2026, month=7)

    assert result.scanned == 1
    assert [item.employee_id for item in result.employees] == ["EMP-SYNTH-001"]


@pytest.mark.asyncio
async def test_service_rejects_inconsistent_payroll_rows_before_rendering_results() -> None:
    adapter = MockDicAdapter()
    adapter._payrolls["EMP-SYNTH-001"].append(
        PayrollMetadata(
            payroll_id="PAY-SYNTH-BAD",
            employee_id="EMP-SYNTH-999",
            year=2026,
            month=7,
        )
    )
    service = DicService(adapter, RuntimeFeatureFlags())

    with pytest.raises(DicValidationError, match="does not match"):
        await service.find_employees_with_payroll(year=2026, month=7)
