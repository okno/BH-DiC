from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from bh_dic.application import BHApplicationCoordinator
from bh_dic.approvals.models import PendingAction
from bh_dic.dic.errors import DicValidationError
from bh_dic.dic.models import ExecutionResult, ReconciliationResult
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import AttachmentPayload, InteractionResult
from bh_dic.services.balance_service import BalanceService
from bh_dic.services.contract_service import ContractService
from bh_dic.services.dic_service import DicService
from bh_dic.services.document_service import DocumentService
from bh_dic.services.employee_service import EmployeeService
from bh_dic.services.invitation_service import InvitationService


def _actor() -> DiscordActor:
    return DiscordActor(
        user_id=1,
        guild_id=2,
        channel_id=3,
        logical_roles=frozenset({"HR_READ"}),
        discord_role_ids=frozenset({4}),
    )


@pytest.mark.asyncio
async def test_read_facades_delegate_to_policy_checked_coordinator() -> None:
    expected = InteractionResult(title="synthetic", description="safe")
    raw = SimpleNamespace(
        employee=AsyncMock(return_value=expected),
        contracts=AsyncMock(return_value=expected),
        documents=AsyncMock(return_value=expected),
        balances=AsyncMock(return_value=expected),
        upload=AsyncMock(return_value=expected),
    )
    coordinator = cast(BHApplicationCoordinator, raw)
    actor = _actor()

    assert await EmployeeService(coordinator).get_summary(actor, "EMP-SYNTH-001") is expected
    raw.employee.assert_awaited_once_with(actor, "EMP-SYNTH-001")

    start = date(2026, 1, 1)
    end = date(2026, 12, 31)
    assert (
        await ContractService(coordinator).list_contracts(
            actor,
            employee_id="EMP-SYNTH-001",
            expiring_from=start,
            expiring_to=end,
        )
        is expected
    )
    raw.contracts.assert_awaited_once_with(actor, "EMP-SYNTH-001", start, end)

    documents = DocumentService(coordinator)
    assert await documents.list_metadata(actor, "EMP-SYNTH-001", status="uploaded") is expected
    raw.documents.assert_awaited_once_with(actor, "EMP-SYNTH-001", "uploaded")

    attachment = AttachmentPayload(
        original_filename="synthetic.pdf",
        content_type="application/pdf",
        declared_size=4,
        content=b"test",
    )
    assert (
        await documents.upload(
            actor,
            "EMP-SYNTH-001",
            "contract",
            attachment,
        )
        is expected
    )
    raw.upload.assert_awaited_once_with(
        actor,
        "EMP-SYNTH-001",
        "contract",
        attachment,
    )

    assert await BalanceService(coordinator).get_balance(actor, "EMP-SYNTH-001", 2026) is expected
    raw.balances.assert_awaited_once_with(actor, "EMP-SYNTH-001", 2026)


@pytest.mark.asyncio
async def test_invitation_facade_uses_authoritative_catalog_and_dic_guards() -> None:
    execution = cast(ExecutionResult, object())
    reconciliation = cast(ReconciliationResult, object())
    raw = SimpleNamespace(
        execute=AsyncMock(return_value=execution),
        reconcile=AsyncMock(return_value=reconciliation),
    )
    service = InvitationService(cast(DicService, raw))
    invitation = cast(PendingAction, SimpleNamespace(function_id="EMP-INVITE-001"))
    parameters = {"delivery": "email"}

    assert await service.execute_approved(invitation, parameters) is execution
    raw.execute.assert_awaited_once_with(invitation, parameters)
    assert await service.reconcile(invitation, parameters) is reconciliation
    raw.reconcile.assert_awaited_once_with(invitation, parameters)

    for function_id in ("EMP-CONTRACT-002", "UNKNOWN"):
        wrong_action = cast(PendingAction, SimpleNamespace(function_id=function_id))
        with pytest.raises(DicValidationError, match="invitation action"):
            await service.execute_approved(wrong_action, {})
    assert raw.execute.await_count == 1
