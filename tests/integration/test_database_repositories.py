from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import URL

from bh_dic.database.engine import Database
from bh_dic.database.models import Approval, DiscordRequest, PendingAction
from bh_dic.database.repositories import (
    ApprovalRepository,
    DiscordRequestRepository,
    FeatureFlagRepository,
    PendingActionRepository,
)


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


def _pending(now: datetime) -> PendingAction:
    return PendingAction(
        action_id="00000000-0000-4000-8000-000000000101",
        correlation_id="corr-repository-action",
        function_id="EMP-CONTRACT-002",
        requester_discord_id="1001",
        guild_id="2001",
        channel_id="3001",
        target_employee_id="EMP-SYNTH-001",
        encrypted_parameters=b"synthetic-ciphertext",
        redacted_diff={"schedule": {"before": "[CURRENT]", "after": "synthetic"}},
        motivation=None,
        state_fingerprint="a" * 64,
        status="PENDING",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        approvals_required=1,
        approvals_received=0,
        confirmation_salt=b"s" * 16,
        confirmation_digest=b"d" * 32,
        confirmation_consumed_at=None,
        idempotency_key="idempotency-repository-0001",
        execution_result=None,
        postcondition_result=None,
        rejection_reason=None,
        version=1,
    )


@pytest.mark.integration
async def test_small_repositories_cover_crud_lookup_order_and_defaults(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "repositories.sqlite3"))
    await database.create_schema()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    try:
        async with database.transaction() as session:
            requests = DiscordRequestRepository(session)
            actions = PendingActionRepository(session)
            approvals = ApprovalRepository(session)
            flags = FeatureFlagRepository(session)

            assert await requests.get_by_correlation_id("corr-missing") is None
            request = await requests.add(
                DiscordRequest(
                    request_id="00000000-0000-4000-8000-000000000201",
                    correlation_id="corr-repository-request",
                    requester_discord_id="1001",
                    guild_id="2001",
                    channel_id="3001",
                    sanitized_request="synthetic request",
                    status="RECEIVED",
                    created_at=now,
                    completed_at=None,
                )
            )
            assert request.request_id.endswith("0201")
            assert (await requests.get_by_correlation_id("corr-repository-request")) is request

            assert await actions.get("missing-action") is None
            assert await actions.get_by_idempotency_key("missing-idempotency") is None
            action = await actions.add(_pending(now))
            assert await actions.get(action.action_id) is action
            assert await actions.get(action.action_id, for_update=True) is action
            assert await actions.get_by_idempotency_key(action.idempotency_key) is action

            later = await approvals.add(
                Approval(
                    approval_id="00000000-0000-4000-8000-000000000302",
                    action_id=action.action_id,
                    approver_discord_id="1003",
                    decision="APPROVE",
                    redacted_reason=None,
                    created_at=now + timedelta(seconds=1),
                )
            )
            earlier = await approvals.add(
                Approval(
                    approval_id="00000000-0000-4000-8000-000000000301",
                    action_id=action.action_id,
                    approver_discord_id="1002",
                    decision="APPROVE",
                    redacted_reason="synthetic approval",
                    created_at=now,
                )
            )
            assert await approvals.list_for_action("missing-action") == ()
            assert await approvals.list_for_action(action.action_id) == (earlier, later)

            assert await flags.is_enabled("ENABLE_SYNTHETIC") is False
            assert await flags.is_enabled("ENABLE_SYNTHETIC", default=True) is True
            created = await flags.set("ENABLE_SYNTHETIC", True, actor_discord_id="1004")
            assert created.enabled is True
            assert await flags.is_enabled("ENABLE_SYNTHETIC") is True
            updated = await flags.set("ENABLE_SYNTHETIC", False, actor_discord_id=None)
            assert updated is created
            assert updated.enabled is False
            assert updated.updated_by_discord_id is None
    finally:
        await database.dispose()
