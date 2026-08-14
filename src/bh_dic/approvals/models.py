"""Approval domain models; no ORM or Discord dependency."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    UNKNOWN_REQUIRES_RECONCILIATION = "UNKNOWN_REQUIRES_RECONCILIATION"
    RECONCILED_NOT_APPLIED = "RECONCILED_NOT_APPLIED"


TERMINAL_STATUSES = frozenset(
    {
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.REJECTED,
        ActionStatus.EXPIRED,
        ActionStatus.STALE,
        ActionStatus.RECONCILED_NOT_APPLIED,
    }
)


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approver_id: str
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: str
    correlation_id: str
    function_id: str
    requester_id: str
    guild_id: str
    channel_id: str
    target_employee_id: str | None
    encrypted_parameters: bytes = field(repr=False)
    redacted_diff: Mapping[str, Any]
    motivation: str | None
    state_fingerprint: str
    status: ActionStatus
    created_at: datetime
    expires_at: datetime
    approvals_required: int
    approvals: tuple[ApprovalRecord, ...]
    confirmation_salt: bytes = field(repr=False)
    confirmation_digest: bytes = field(repr=False)
    confirmation_consumed_at: datetime | None
    idempotency_key: str
    execution_result: str | None = None
    postcondition_result: str | None = None
    rejection_reason: str | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    version: int = 1

    @property
    def confirmed(self) -> bool:
        return self.confirmation_consumed_at is not None

    @property
    def approved_by(self) -> frozenset[str]:
        return frozenset(record.approver_id for record in self.approvals)


@dataclass(frozen=True, slots=True)
class PreparedAction:
    """The confirmation code exists only in this one response object."""

    action: PendingAction
    confirmation_code: str = field(repr=False)
    required_text_confirmation: str | None = None
