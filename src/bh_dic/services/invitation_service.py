"""Invitation execution facade over the approved DIC write boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bh_dic.approvals.models import PendingAction
from bh_dic.dic.errors import DicValidationError
from bh_dic.dic.models import ExecutionResult, ReconciliationResult
from bh_dic.policies.catalog import get_function_spec
from bh_dic.services.dic_service import DicService

_INVITATION_FEATURE_FLAG = "ENABLE_INVITE_ACTIONS"


class InvitationService:
    """Execute or reconcile only already-approved invitation actions.

    Classification is read from the authoritative policy catalog through the invitation
    feature flag. The facade deliberately carries no duplicate list of Function IDs.
    """

    def __init__(self, dic: DicService) -> None:
        self._dic = dic

    @staticmethod
    def _require_invitation(action: PendingAction) -> None:
        spec = get_function_spec(action.function_id)
        if spec is None or _INVITATION_FEATURE_FLAG not in spec.feature_flags:
            raise DicValidationError("an invitation action from the policy catalog is required")

    async def execute_approved(
        self,
        action: PendingAction,
        parameters: Mapping[str, Any],
    ) -> ExecutionResult:
        """Execute an invitation after DicService rechecks approval and feature flags."""

        self._require_invitation(action)
        return await self._dic.execute(action, parameters)

    async def reconcile(
        self,
        action: PendingAction,
        parameters: Mapping[str, Any],
    ) -> ReconciliationResult:
        """Reconcile an uncertain invitation without retrying the write."""

        self._require_invitation(action)
        return await self._dic.reconcile(action, parameters)
