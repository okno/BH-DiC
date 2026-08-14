from __future__ import annotations

from dataclasses import replace

import pytest

from bh_dic.policies.catalog import (
    ALL_FUNCTION_IDS,
    FUNCTION_CATALOG,
    READ_FUNCTION_IDS,
    WRITE_FUNCTION_IDS,
    WriteParameterValidationError,
    validate_write_parameters,
)
from bh_dic.policies.decisions import DecisionCode
from bh_dic.policies.engine import PolicyContext, PolicyEngine, PolicyPhase
from bh_dic.policies.feature_flags import DEFAULT_FEATURE_FLAGS, RuntimeFeatureFlags
from bh_dic.policies.roles import LogicalRole

EXPECTED_IDS = frozenset(
    {
        "EMP-READ-001",
        "EMP-READ-002",
        "EMP-SEARCH-001",
        "EMP-FILTER-001",
        "EMP-SORT-001",
        "EMP-PAGE-001",
        "EMP-CONTRACT-001",
        "EMP-RBAC-001",
        "EMP-TIME-001",
        "EMP-MAT-001",
        "EMP-BAL-001",
        "EMP-PAY-001",
        "EMP-DOC-001",
        "EMP-UPDATE-001",
        "EMP-CREATE-001",
        "EMP-CONTRACT-002",
        "EMP-MAT-002",
        "EMP-BAL-002",
        "EMP-CONNECT-001",
        "EMP-CONNECT-002",
        "EMP-INVITE-001",
        "EMP-INVITE-002",
        "EMP-RBAC-002",
        "EMP-STATUS-001",
        "EMP-STATUS-002",
        "EMP-DOC-002",
        "EMP-DOC-003",
        "EMP-DOC-004",
        "EMP-DOC-005",
        "EMP-EXPORT-001",
        "EMP-DELETE-001",
        "EMP-CONTRACT-003",
    }
)

VALID_WRITE_PARAMETERS: dict[str, dict[str, object]] = {
    "EMP-UPDATE-001": {"job_title": "Lead"},
    "EMP-CREATE-001": {"first_name": "Nome", "last_name": "Esempio"},
    "EMP-CONTRACT-002": {"schedule": "full-time"},
    "EMP-MAT-002": {"category": "Ferie"},
    "EMP-BAL-002": {
        "year": 2026,
        "month": 8,
        "category": "Ferie",
        "previous_value": "0",
        "amount": "1.5",
        "motivation": "Correzione autorizzata",
    },
    "EMP-CONNECT-001": {},
    "EMP-CONNECT-002": {"motivation": "Disconnessione autorizzata"},
    "EMP-INVITE-001": {},
    "EMP-INVITE-002": {},
    "EMP-RBAC-002": {
        "role_name": "Employee",
        "enabled": False,
        "motivation": "Cambio autorizzato",
    },
    "EMP-STATUS-001": {"motivation": "Disattivazione autorizzata"},
    "EMP-STATUS-002": {},
    "EMP-DOC-002": {
        "upload_id": "0" * 32,
        "category": "contract",
    },
    "EMP-DOC-004": {"document_id": "DOC-1", "category": "contract"},
    "EMP-DOC-005": {
        "document_id": "DOC-1",
        "motivation": "Eliminazione autorizzata",
    },
    "EMP-EXPORT-001": {"scope": "employees"},
    "EMP-DOC-003": {
        "document_id": "DOC-1",
        "motivation": "Download autorizzato",
    },
    "EMP-DELETE-001": {"motivation": "Eliminazione autorizzata"},
    "EMP-CONTRACT-003": {
        "contract_id": "CON-1",
        "motivation": "Eliminazione autorizzata",
    },
}


def _flags(**overrides: bool) -> RuntimeFeatureFlags:
    baseline = dict(DEFAULT_FEATURE_FLAGS)
    baseline.update(overrides)
    return RuntimeFeatureFlags(baseline)


def _context(
    function_id: str,
    *,
    roles: frozenset[LogicalRole] = frozenset({LogicalRole.HR_READ}),
    flags: RuntimeFeatureFlags | None = None,
    scope: str = "default",
    phase: PolicyPhase = PolicyPhase.PREPARE,
    target: str | None = "employee-1",
    entitlements: frozenset[str] = frozenset(),
    capabilities: frozenset[str] = frozenset(),
) -> PolicyContext:
    return PolicyContext(
        function_id=function_id,
        user_id="user-1",
        guild_id="guild-1",
        channel_id="channel-1",
        allowed_guild_ids=frozenset({"guild-1"}),
        allowed_channel_ids=frozenset({"channel-1"}),
        roles=roles,
        flags=flags or _flags(),
        current_tenant_id="tenant-1",
        allowed_tenant_ids=frozenset({"tenant-1"}),
        phase=phase,
        operation_scope=scope,
        entitlements=entitlements,
        target_employee_id=target,
        system_capabilities=capabilities,
    )


def test_policy_catalog_is_complete_unique_and_safe_by_default() -> None:
    assert ALL_FUNCTION_IDS == EXPECTED_IDS
    assert READ_FUNCTION_IDS | WRITE_FUNCTION_IDS == EXPECTED_IDS
    assert READ_FUNCTION_IDS.isdisjoint(WRITE_FUNCTION_IDS)
    assert all(FUNCTION_CATALOG[item].enabled_by_default for item in READ_FUNCTION_IDS)
    assert all(not FUNCTION_CATALOG[item].enabled_by_default for item in WRITE_FUNCTION_IDS)
    assert all(
        "ENABLE_WRITE_ACTIONS" in FUNCTION_CATALOG[item].feature_flags
        for item in WRITE_FUNCTION_IDS
    )


@pytest.mark.parametrize("function_id", sorted(WRITE_FUNCTION_IDS))
def test_every_write_has_one_closed_authoritative_schema(function_id: str) -> None:
    spec = FUNCTION_CATALOG[function_id]
    assert spec.resource_snapshot is not None
    assert spec.write_parameters
    validated = validate_write_parameters(spec, VALID_WRITE_PARAMETERS[function_id])
    assert set(validated).issubset({parameter.name for parameter in spec.write_parameters})
    with pytest.raises(WriteParameterValidationError, match="unsupported write parameters"):
        validate_write_parameters(spec, {**VALID_WRITE_PARAMETERS[function_id], "extra": "x"})


@pytest.mark.parametrize(
    ("function_id", "parameters"),
    [
        ("EMP-UPDATE-001", {}),
        ("EMP-CONTRACT-002", {"contract_id": "CON-1"}),
        ("EMP-DOC-004", {"document_id": "DOC-1"}),
    ],
)
def test_write_schema_rejects_empty_mutations(
    function_id: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(WriteParameterValidationError, match="one write parameter is required"):
        validate_write_parameters(FUNCTION_CATALOG[function_id], parameters)


def test_zero_argument_actions_allow_only_motivation_and_upload_never_accepts_a_path() -> None:
    connect = FUNCTION_CATALOG["EMP-CONNECT-001"]
    assert validate_write_parameters(connect, {}) == {}
    with pytest.raises(WriteParameterValidationError, match="unsupported write parameters"):
        validate_write_parameters(connect, {"status": "connected"})
    with pytest.raises(WriteParameterValidationError, match="unsupported write parameters"):
        validate_write_parameters(
            FUNCTION_CATALOG["EMP-DOC-002"],
            {
                **VALID_WRITE_PARAMETERS["EMP-DOC-002"],
                "safe_local_path": "C:/not-allowed",
            },
        )


def test_high_risk_catalog_entries_require_a2_and_are_not_model_exposed() -> None:
    for function_id in {
        "EMP-DOC-003",
        "EMP-DELETE-001",
        "EMP-CONTRACT-003",
        "EMP-BAL-002",
        "EMP-RBAC-002",
    }:
        spec = FUNCTION_CATALOG[function_id]
        assert spec.approvals_required == 2
        assert spec.expose_to_model is False
        assert spec.destructive is True


def test_live_routes_without_a_provable_postcondition_are_blocked_before_pending() -> None:
    unavailable = {
        function_id
        for function_id in WRITE_FUNCTION_IDS
        if not FUNCTION_CATALOG[function_id].operator_live_available
    }
    assert unavailable == {
        "EMP-INVITE-001",
        "EMP-DOC-003",
        "EMP-DOC-005",
        "EMP-EXPORT-001",
        "EMP-CONTRACT-003",
    }


@pytest.mark.parametrize(
    ("function_id", "parameter"),
    [("EMP-DOC-003", "document_id"), ("EMP-CONTRACT-003", "contract_id")],
)
def test_unavailable_destructive_routes_still_validate_stable_identifier_syntax(
    function_id: str, parameter: str
) -> None:
    invalid = dict(VALID_WRITE_PARAMETERS[function_id])
    invalid[parameter] = "../ambiguous target"
    with pytest.raises(WriteParameterValidationError, match="invalid syntax"):
        validate_write_parameters(FUNCTION_CATALOG[function_id], invalid)


def test_readonly_can_count_but_cannot_list_or_read_detail() -> None:
    engine = PolicyEngine()
    aggregate = _context(
        "EMP-READ-001",
        roles=frozenset({LogicalRole.READ_ONLY}),
        scope="aggregate",
        target=None,
    )
    assert engine.evaluate(aggregate).allowed
    denied_list = replace(aggregate, operation_scope="default")
    assert engine.evaluate(denied_list).code == DecisionCode.ROLE_DENIED
    detail = replace(aggregate, function_id="EMP-READ-002", operation_scope="default")
    assert engine.evaluate(detail).code == DecisionCode.ROLE_DENIED


def test_policy_rechecks_allowlists_tenant_entitlement_and_target() -> None:
    engine = PolicyEngine()
    base = _context("EMP-BAL-001", entitlements=frozenset({"balances:read"}))
    assert engine.evaluate(base).allowed
    assert engine.evaluate(replace(base, guild_id="other")).code == DecisionCode.GUILD_DENIED
    assert engine.evaluate(replace(base, channel_id="other")).code == DecisionCode.CHANNEL_DENIED
    assert engine.evaluate(replace(base, current_tenant_id=None)).code == DecisionCode.TENANT_DENIED
    assert engine.evaluate(replace(base, entitlements=frozenset())).code == DecisionCode.ROLE_DENIED
    assert (
        engine.evaluate(replace(base, target_employee_id=None)).code == DecisionCode.TARGET_REQUIRED
    )


def test_write_requires_global_and_specific_flags_and_sensitive_extra_flag() -> None:
    engine = PolicyEngine()
    disabled = _context("EMP-UPDATE-001", roles=frozenset({LogicalRole.HR_WRITE}))
    assert engine.evaluate(disabled).code == DecisionCode.FEATURE_DISABLED
    enabled_flags = _flags(ENABLE_WRITE_ACTIONS=True, ENABLE_EMPLOYEE_UPDATE=True)
    enabled = replace(disabled, flags=enabled_flags)
    assert engine.evaluate(enabled).allowed
    sensitive = replace(enabled, sensitive_profile=True)
    assert engine.evaluate(sensitive).code == DecisionCode.FEATURE_DISABLED
    all_enabled = replace(
        sensitive,
        flags=_flags(
            ENABLE_WRITE_ACTIONS=True,
            ENABLE_EMPLOYEE_UPDATE=True,
            ENABLE_SENSITIVE_PROFILE_UPDATE=True,
        ),
    )
    assert engine.evaluate(all_enabled).allowed


def test_runtime_flag_can_disable_but_never_enable_baseline() -> None:
    flags = _flags(ENABLE_WRITE_ACTIONS=True, ENABLE_EMPLOYEE_UPDATE=True)
    assert flags.enabled("ENABLE_WRITE_ACTIONS")
    flags.set_runtime("ENABLE_WRITE_ACTIONS", False)
    assert not flags.enabled("ENABLE_WRITE_ACTIONS")
    flags.set_runtime("ENABLE_WRITE_ACTIONS", True)
    assert flags.enabled("ENABLE_WRITE_ACTIONS")
    disabled = _flags(ENABLE_WRITE_ACTIONS=False)
    disabled.set_runtime("ENABLE_WRITE_ACTIONS", True)
    assert not disabled.enabled("ENABLE_WRITE_ACTIONS")


def test_upload_fails_closed_without_clamav_and_critical_tools_stay_hidden() -> None:
    engine = PolicyEngine()
    flags = _flags(ENABLE_WRITE_ACTIONS=True, ENABLE_DOCUMENT_UPLOAD=True)
    upload = _context(
        "EMP-DOC-002",
        roles=frozenset({LogicalRole.DOCUMENT_OPERATOR}),
        flags=flags,
    )
    assert engine.evaluate(upload).code == DecisionCode.CAPABILITY_UNAVAILABLE
    assert engine.evaluate(replace(upload, system_capabilities=frozenset({"clamav"}))).allowed

    destructive_flags = _flags(ENABLE_WRITE_ACTIONS=True, ENABLE_EMPLOYEE_DELETE=True)
    destructive = _context(
        "EMP-DELETE-001",
        roles=frozenset({LogicalRole.SYSTEM_ADMIN}),
        flags=destructive_flags,
        phase=PolicyPhase.EXPOSURE,
    )
    assert engine.evaluate(destructive).code == DecisionCode.NOT_EXPOSED_TO_MODEL


def test_export_rbac_depends_on_protected_output_source() -> None:
    engine = PolicyEngine()
    flags = _flags(ENABLE_WRITE_ACTIONS=True, ENABLE_EXPORT=True)
    hr = _context(
        "EMP-EXPORT-001",
        roles=frozenset({LogicalRole.HR_READ}),
        flags=flags,
        target=None,
    )
    assert engine.evaluate(hr).code == DecisionCode.ROLE_DENIED
    assert engine.evaluate(replace(hr, operation_scope="employees")).allowed
    assert (
        engine.evaluate(replace(hr, operation_scope="documents")).code == DecisionCode.ROLE_DENIED
    )
    assert engine.evaluate(replace(hr, operation_scope="balances")).code == DecisionCode.ROLE_DENIED
    assert engine.evaluate(
        replace(
            hr,
            operation_scope="balances",
            entitlements=frozenset({"balances:read"}),
        )
    ).allowed
    document_operator = replace(
        hr,
        roles=frozenset({LogicalRole.DOCUMENT_OPERATOR}),
        operation_scope="documents",
    )
    assert engine.evaluate(document_operator).allowed


def test_model_visibility_uses_exposure_scope_and_rejects_unknown_roles() -> None:
    engine = PolicyEngine()
    readonly = _context(
        "EMP-READ-001",
        roles=frozenset({LogicalRole.READ_ONLY}),
        target=None,
    )
    assert "EMP-READ-001" in engine.visible_function_ids(readonly)
    invalid = replace(readonly, roles=frozenset({"NOT_A_ROLE"}))
    assert engine.evaluate(invalid).code == DecisionCode.ROLE_DENIED
