"""Deterministic policy evaluation independent from Discord and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from bh_dic.policies.catalog import FUNCTION_CATALOG
from bh_dic.policies.decisions import DecisionCode, PolicyDecision
from bh_dic.policies.feature_flags import FeatureFlags
from bh_dic.policies.roles import LogicalRole, normalize_roles


class PolicyPhase(StrEnum):
    EXPOSURE = "EXPOSURE"
    PREPARE = "PREPARE"
    EXECUTE = "EXECUTE"


@dataclass(frozen=True, slots=True)
class PolicyContext:
    function_id: str
    user_id: str
    guild_id: str
    channel_id: str
    allowed_guild_ids: frozenset[str]
    allowed_channel_ids: frozenset[str]
    roles: frozenset[LogicalRole | str]
    flags: FeatureFlags
    current_tenant_id: str | None
    allowed_tenant_ids: frozenset[str]
    phase: PolicyPhase = PolicyPhase.PREPARE
    operation_scope: str = "default"
    entitlements: frozenset[str] = field(default_factory=frozenset)
    target_employee_id: str | None = None
    target_ambiguous: bool = False
    sensitive_profile: bool = False
    system_state: str = "HEALTHY"
    system_capabilities: frozenset[str] = field(default_factory=frozenset)


class PolicyEngine:
    """Evaluate all gates on every phase; no Discord check is trusted alone."""

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        function_id = context.function_id
        spec = FUNCTION_CATALOG.get(function_id)
        if spec is None:
            return PolicyDecision.deny(
                function_id, DecisionCode.UNKNOWN_FUNCTION, "unknown Function ID"
            )
        if not context.user_id or not context.guild_id or not context.channel_id:
            return PolicyDecision.deny(
                function_id, DecisionCode.INVALID_CONTEXT, "missing actor context"
            )
        if context.guild_id not in context.allowed_guild_ids:
            return PolicyDecision.deny(
                function_id, DecisionCode.GUILD_DENIED, "guild is not allowlisted"
            )
        if context.channel_id not in context.allowed_channel_ids:
            return PolicyDecision.deny(
                function_id, DecisionCode.CHANNEL_DENIED, "channel is not allowlisted"
            )
        if (
            context.current_tenant_id is None
            or context.current_tenant_id not in context.allowed_tenant_ids
        ):
            return PolicyDecision.deny(
                function_id, DecisionCode.TENANT_DENIED, "tenant is not verified"
            )
        if context.system_state != "HEALTHY":
            return PolicyDecision.deny(
                function_id, DecisionCode.SYSTEM_DEGRADED, "system is not healthy"
            )
        if context.phase == PolicyPhase.EXPOSURE and not spec.expose_to_model:
            return PolicyDecision.deny(
                function_id,
                DecisionCode.NOT_EXPOSED_TO_MODEL,
                "critical function is not exposed to the model",
            )
        for flag in spec.feature_flags:
            if not context.flags.enabled(flag):
                return PolicyDecision.deny(
                    function_id,
                    DecisionCode.FEATURE_DISABLED,
                    f"required feature flag is disabled: {flag}",
                )
        if context.sensitive_profile and not context.flags.enabled(
            "ENABLE_SENSITIVE_PROFILE_UPDATE"
        ):
            return PolicyDecision.deny(
                function_id,
                DecisionCode.FEATURE_DISABLED,
                "sensitive profile updates are disabled",
            )
        missing = spec.required_capabilities - context.system_capabilities
        if missing:
            return PolicyDecision.deny(
                function_id,
                DecisionCode.CAPABILITY_UNAVAILABLE,
                "required system capability is unavailable",
            )
        try:
            roles = normalize_roles(context.roles)
        except ValueError:
            return PolicyDecision.deny(
                function_id, DecisionCode.ROLE_DENIED, "unknown logical role"
            )
        role_rule = spec.role_rule(context.operation_scope)
        if role_rule is None or not role_rule.matches(roles, context.entitlements):
            return PolicyDecision.deny(
                function_id, DecisionCode.ROLE_DENIED, "logical role policy denied"
            )
        if context.phase != PolicyPhase.EXPOSURE:
            if spec.requires_target and not context.target_employee_id:
                return PolicyDecision.deny(
                    function_id, DecisionCode.TARGET_REQUIRED, "employee ID is required"
                )
            if spec.is_write and context.target_ambiguous:
                return PolicyDecision.deny(
                    function_id,
                    DecisionCode.TARGET_AMBIGUOUS,
                    "ambiguous write target",
                )
        return PolicyDecision.allow(function_id)

    def visible_function_ids(self, context: PolicyContext) -> frozenset[str]:
        """Return model-visible IDs using the exact same authorization gates."""

        visible: set[str] = set()
        for function_id in FUNCTION_CATALOG:
            candidate = PolicyContext(
                function_id=function_id,
                user_id=context.user_id,
                guild_id=context.guild_id,
                channel_id=context.channel_id,
                allowed_guild_ids=context.allowed_guild_ids,
                allowed_channel_ids=context.allowed_channel_ids,
                roles=context.roles,
                flags=context.flags,
                current_tenant_id=context.current_tenant_id,
                allowed_tenant_ids=context.allowed_tenant_ids,
                phase=PolicyPhase.EXPOSURE,
                operation_scope="exposure",
                entitlements=context.entitlements,
                system_state=context.system_state,
                system_capabilities=context.system_capabilities,
            )
            if self.evaluate(candidate).allowed:
                visible.add(function_id)
        return frozenset(visible)
