"""Persistent-ready approval workflow with an in-memory reference store."""

from bh_dic.approvals.confirmation import ConfirmationHasher
from bh_dic.approvals.models import (
    ActionStatus,
    ApprovalRecord,
    PendingAction,
    PreparedAction,
)
from bh_dic.approvals.service import (
    ApprovalError,
    ApprovalService,
    AuthorizationError,
    DuplicateExecutionError,
    ExpiredActionError,
    InvalidConfirmationError,
    InvalidStateError,
    StaleTargetError,
    WriteDisabledError,
)
from bh_dic.approvals.storage import (
    ApprovalRepository,
    InMemoryApprovalRepository,
    SqlAlchemyApprovalRepository,
)

__all__ = [
    "ActionStatus",
    "ApprovalError",
    "ApprovalRecord",
    "ApprovalRepository",
    "ApprovalService",
    "AuthorizationError",
    "ConfirmationHasher",
    "DuplicateExecutionError",
    "ExpiredActionError",
    "InMemoryApprovalRepository",
    "InvalidConfirmationError",
    "InvalidStateError",
    "PendingAction",
    "PreparedAction",
    "SqlAlchemyApprovalRepository",
    "StaleTargetError",
    "WriteDisabledError",
]
