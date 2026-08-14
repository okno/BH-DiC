"""Approval state machine enforcing TTL, separation of duty and idempotency."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from bh_dic.approvals.confirmation import ConfirmationHasher
from bh_dic.approvals.models import ActionStatus, ApprovalRecord, PendingAction, PreparedAction
from bh_dic.approvals.storage import ApprovalRepository
from bh_dic.policies.catalog import FunctionSpec, get_function_spec
from bh_dic.policies.roles import LogicalRole, normalize_roles
from bh_dic.security.sanitization import normalize_text


class ApprovalError(RuntimeError):
    pass


class ActionNotFoundError(ApprovalError):
    pass


class InvalidStateError(ApprovalError):
    pass


class AuthorizationError(ApprovalError):
    pass


class ExpiredActionError(ApprovalError):
    pass


class InvalidConfirmationError(ApprovalError):
    pass


class DuplicateExecutionError(ApprovalError):
    pass


class WriteDisabledError(ApprovalError):
    pass


class StaleTargetError(ApprovalError):
    pass


class ApprovalService:
    """Single-node state machine; a DB repository must retain atomic CAS semantics."""

    def __init__(
        self,
        repository: ApprovalRepository,
        confirmation_hasher: ConfirmationHasher,
        *,
        writes_enabled: Callable[[], bool],
        default_ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if default_ttl <= timedelta(0):
            raise ValueError("approval TTL must be positive")
        self._repository = repository
        self._confirmation = confirmation_hasher
        self._writes_enabled = writes_enabled
        self._default_ttl = default_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mutation_lock = asyncio.Lock()

    async def prepare(
        self,
        *,
        function_id: str,
        correlation_id: str,
        requester_id: str,
        guild_id: str,
        channel_id: str,
        target_employee_id: str | None,
        encrypted_parameters: bytes,
        redacted_diff: Mapping[str, Any],
        state_fingerprint: str,
        motivation: str | None = None,
        ttl: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> PreparedAction:
        self._require_writes_enabled()
        spec = get_function_spec(function_id)
        if spec is None or not spec.is_write:
            raise ValueError("pending actions require a known write Function ID")
        if spec.requires_target and not target_employee_id:
            raise ValueError("target employee ID is required")
        if not all((correlation_id, requester_id, guild_id, channel_id, state_fingerprint)):
            raise ValueError("pending action context cannot be empty")
        cleaned_motivation = (
            normalize_text(motivation, max_length=500, allow_newlines=True) if motivation else None
        )
        if spec.destructive and not cleaned_motivation:
            raise ValueError("critical actions require a motivation")
        if not encrypted_parameters:
            raise ValueError("encrypted pending parameters cannot be empty")
        effective_ttl = ttl or self._default_ttl
        if effective_ttl <= timedelta(0):
            raise ValueError("approval TTL must be positive")
        now = self._now()
        action_id = str(uuid.uuid4())
        material = self._confirmation.create(action_id)
        text_confirmation = (
            spec.text_confirmation_template.format(employee_id=target_employee_id)
            if spec.text_confirmation_template
            else None
        )
        effective_idempotency_key = idempotency_key or secrets.token_urlsafe(32)
        if not 16 <= len(effective_idempotency_key) <= 200:
            raise ValueError("idempotency key must contain 16..200 characters")
        action = PendingAction(
            action_id=action_id,
            correlation_id=correlation_id,
            function_id=function_id,
            requester_id=requester_id,
            guild_id=guild_id,
            channel_id=channel_id,
            target_employee_id=target_employee_id,
            encrypted_parameters=bytes(encrypted_parameters),
            redacted_diff=dict(redacted_diff),
            motivation=cleaned_motivation,
            state_fingerprint=state_fingerprint,
            status=ActionStatus.PENDING,
            created_at=now,
            expires_at=now + effective_ttl,
            approvals_required=spec.approvals_required,
            approvals=(),
            confirmation_salt=material.salt,
            confirmation_digest=material.digest,
            confirmation_consumed_at=None,
            idempotency_key=effective_idempotency_key,
        )
        await self._repository.insert(action)
        return PreparedAction(action, material.code, text_confirmation)

    async def get(self, action_id: str) -> PendingAction:
        action = await self._repository.get(action_id)
        if action is None:
            raise ActionNotFoundError("pending action does not exist")
        return action

    async def confirm(
        self,
        action_id: str,
        *,
        requester_id: str,
        confirmation_code: str,
        text_confirmation: str | None = None,
    ) -> PendingAction:
        self._require_writes_enabled()
        async with self._mutation_lock:
            action = await self.get(action_id)
            now = self._now()
            action = await self._require_active(action, now)
            if requester_id != action.requester_id:
                raise AuthorizationError("only the requester can consume the confirmation code")
            if action.confirmed:
                raise InvalidConfirmationError("confirmation code was already consumed")
            if not self._confirmation.verify(
                action.action_id,
                action.confirmation_salt,
                action.confirmation_digest,
                confirmation_code,
            ):
                raise InvalidConfirmationError("invalid confirmation code")
            spec = self._require_function_spec(action)
            if spec.text_confirmation_template:
                expected = spec.text_confirmation_template.format(
                    employee_id=action.target_employee_id
                )
                if text_confirmation != expected:
                    raise InvalidConfirmationError("explicit target confirmation does not match")
            status = (
                ActionStatus.APPROVED if action.approvals_required == 0 else ActionStatus.PENDING
            )
            updated = replace(
                action,
                confirmation_consumed_at=now,
                status=status,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def approve(
        self,
        action_id: str,
        *,
        approver_id: str,
        approver_roles: frozenset[LogicalRole | str],
    ) -> PendingAction:
        self._require_writes_enabled()
        async with self._mutation_lock:
            action = await self.get(action_id)
            now = self._now()
            action = await self._require_active(action, now)
            if not action.confirmed:
                raise InvalidStateError("requester confirmation is required first")
            if action.approvals_required == 0:
                raise InvalidStateError("this action does not require an approver")
            if action.status == ActionStatus.APPROVED:
                raise InvalidStateError("action is already fully approved")
            if approver_id == action.requester_id:
                raise AuthorizationError("requester cannot approve their own action")
            if approver_id in action.approved_by:
                raise AuthorizationError("approver already recorded")
            spec = self._require_function_spec(action)
            try:
                roles = normalize_roles(approver_roles)
            except ValueError as exc:
                raise AuthorizationError("unknown logical approver role") from exc
            if spec.approver_roles.isdisjoint(roles):
                raise AuthorizationError("actor does not have an eligible approver role")
            approvals = (*action.approvals, ApprovalRecord(approver_id, now))
            status = (
                ActionStatus.APPROVED
                if len(approvals) >= action.approvals_required
                else ActionStatus.PARTIALLY_APPROVED
            )
            updated = replace(
                action,
                approvals=approvals,
                status=status,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def reject(
        self,
        action_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[LogicalRole | str],
        reason: str,
    ) -> PendingAction:
        async with self._mutation_lock:
            action = await self.get(action_id)
            now = self._now()
            action = await self._require_active(action, now)
            spec = self._require_function_spec(action)
            try:
                roles = normalize_roles(actor_roles)
            except ValueError as exc:
                raise AuthorizationError("unknown logical approver role") from exc
            if actor_id in action.approved_by:
                raise InvalidStateError("a recorded approval cannot be changed to rejection")
            if actor_id != action.requester_id and spec.approver_roles.isdisjoint(roles):
                raise AuthorizationError("actor cannot reject this action")
            cleaned_reason = normalize_text(reason, max_length=500, allow_newlines=True)
            if not cleaned_reason:
                raise ValueError("rejection reason must contain 1..500 characters")
            updated = replace(
                action,
                status=ActionStatus.REJECTED,
                rejection_reason=cleaned_reason,
                rejected_by=actor_id,
                rejected_at=now,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def begin_execution(
        self,
        action_id: str,
        *,
        current_state_fingerprint: str,
    ) -> PendingAction:
        """Atomically claim an approved action after rechecking the kill switch."""

        self._require_writes_enabled()
        async with self._mutation_lock:
            action = await self.get(action_id)
            if action.status in {
                ActionStatus.EXECUTING,
                ActionStatus.SUCCEEDED,
                ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION,
                ActionStatus.RECONCILED_NOT_APPLIED,
            }:
                raise DuplicateExecutionError("action execution was already claimed")
            now = self._now()
            action = await self._require_active(action, now)
            if action.status != ActionStatus.APPROVED:
                raise InvalidStateError("action is not fully approved")
            if current_state_fingerprint != action.state_fingerprint:
                stale = replace(action, status=ActionStatus.STALE, version=action.version + 1)
                await self._repository.replace(stale, expected_version=action.version)
                raise StaleTargetError("target state changed after preview")
            if not await self._repository.claim_idempotency(
                action.idempotency_key, action.action_id
            ):
                raise DuplicateExecutionError("idempotency key was already claimed")
            executing = replace(
                action,
                status=ActionStatus.EXECUTING,
                version=action.version + 1,
            )
            await self._repository.replace(executing, expected_version=action.version)
            return executing

    async def complete_success(
        self,
        action_id: str,
        *,
        execution_result: str,
        postcondition_result: str,
        postcondition_verified: bool,
    ) -> PendingAction:
        async with self._mutation_lock:
            action = await self.get(action_id)
            if action.status != ActionStatus.EXECUTING:
                raise InvalidStateError("only an executing action can complete")
            status = (
                ActionStatus.SUCCEEDED
                if postcondition_verified
                else ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION
            )
            updated = replace(
                action,
                status=status,
                execution_result=execution_result,
                postcondition_result=postcondition_result,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def complete_failure(
        self,
        action_id: str,
        *,
        result: str,
        outcome_uncertain: bool,
    ) -> PendingAction:
        async with self._mutation_lock:
            action = await self.get(action_id)
            if action.status != ActionStatus.EXECUTING:
                raise InvalidStateError("only an executing action can fail")
            updated = replace(
                action,
                status=(
                    ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION
                    if outcome_uncertain
                    else ActionStatus.FAILED
                ),
                execution_result=result,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def reconcile(
        self,
        action_id: str,
        *,
        postcondition_met: bool,
        result: str,
    ) -> PendingAction:
        async with self._mutation_lock:
            action = await self.get(action_id)
            if action.status != ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION:
                raise InvalidStateError("only an uncertain action can be reconciled")
            updated = replace(
                action,
                status=(
                    ActionStatus.SUCCEEDED
                    if postcondition_met
                    else ActionStatus.RECONCILED_NOT_APPLIED
                ),
                postcondition_result=result,
                version=action.version + 1,
            )
            await self._repository.replace(updated, expected_version=action.version)
            return updated

    async def expire_pending(self) -> int:
        count = 0
        async with self._mutation_lock:
            now = self._now()
            for action in await self._repository.list_actions():
                if (
                    action.status
                    in {
                        ActionStatus.PENDING,
                        ActionStatus.PARTIALLY_APPROVED,
                        ActionStatus.APPROVED,
                    }
                    and now >= action.expires_at
                ):
                    expired = replace(
                        action,
                        status=ActionStatus.EXPIRED,
                        version=action.version + 1,
                    )
                    await self._repository.replace(expired, expected_version=action.version)
                    count += 1
        return count

    async def _require_active(self, action: PendingAction, now: datetime) -> PendingAction:
        if now >= action.expires_at and action.status in {
            ActionStatus.PENDING,
            ActionStatus.PARTIALLY_APPROVED,
            ActionStatus.APPROVED,
        }:
            expired = replace(action, status=ActionStatus.EXPIRED, version=action.version + 1)
            await self._repository.replace(expired, expected_version=action.version)
            raise ExpiredActionError("pending action expired")
        if action.status not in {
            ActionStatus.PENDING,
            ActionStatus.PARTIALLY_APPROVED,
            ActionStatus.APPROVED,
        }:
            raise InvalidStateError(f"action is not active: {action.status}")
        return action

    def _require_writes_enabled(self) -> None:
        if not self._writes_enabled():
            raise WriteDisabledError("global write kill switch is disabled")

    @staticmethod
    def _require_function_spec(action: PendingAction) -> FunctionSpec:
        spec = get_function_spec(action.function_id)
        if spec is None or not spec.is_write:
            raise InvalidStateError("pending action references an unknown write function")
        return spec

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("approval clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
