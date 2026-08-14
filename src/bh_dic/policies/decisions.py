"""Typed policy outcomes suitable for audit without sensitive payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DecisionCode(StrEnum):
    ALLOWED = "ALLOWED"
    UNKNOWN_FUNCTION = "UNKNOWN_FUNCTION"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    GUILD_DENIED = "GUILD_DENIED"
    CHANNEL_DENIED = "CHANNEL_DENIED"
    TENANT_DENIED = "TENANT_DENIED"
    ROLE_DENIED = "ROLE_DENIED"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    SYSTEM_DEGRADED = "SYSTEM_DEGRADED"
    TARGET_REQUIRED = "TARGET_REQUIRED"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    NOT_EXPOSED_TO_MODEL = "NOT_EXPOSED_TO_MODEL"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: DecisionCode
    function_id: str
    reason: str

    @classmethod
    def allow(cls, function_id: str) -> PolicyDecision:
        return cls(True, DecisionCode.ALLOWED, function_id, "policy checks passed")

    @classmethod
    def deny(
        cls,
        function_id: str,
        code: DecisionCode,
        reason: str,
    ) -> PolicyDecision:
        return cls(False, code, function_id, reason)
