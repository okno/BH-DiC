from __future__ import annotations

import re

import pytest

from bh_dic.application import (
    ApplicationError,
    ApplicationPolicyDenied,
    ApplicationScope,
    BHApplicationCoordinator,
)
from bh_dic.approvals.confirmation import ConfirmationHasher
from bh_dic.approvals.service import ApprovalService
from bh_dic.approvals.storage import InMemoryApprovalRepository
from bh_dic.dic.errors import DicAmbiguousWriteOutcomeError
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import (
    EmployeeListQuery,
    EmployeeListResult,
    PreparedAction,
    ReconciliationResult,
    ReconciliationState,
    SortDirection,
)
from bh_dic.discord.checks import DiscordActor
from bh_dic.openai.client import RoutedIntent
from bh_dic.openai.schemas import (
    ActionClass,
    IntentEnvelope,
    RouteMetadata,
    Sensitivity,
)
from bh_dic.policies.engine import PolicyEngine
from bh_dic.policies.feature_flags import DEFAULT_FEATURE_FLAGS, RuntimeFeatureFlags
from bh_dic.policies.roles import LogicalRole
from bh_dic.security.cipher import PayloadCipher
from bh_dic.services.dic_service import DicService


class FixedRouter:
    def __init__(self, envelope: IntentEnvelope) -> None:
        self.envelope = envelope
        self.exposed: frozenset[str] | None = None

    async def route(self, request: str, allowed_function_ids: frozenset[str]) -> RoutedIntent:
        del request
        self.exposed = allowed_function_ids
        return RoutedIntent(
            self.envelope,
            RouteMetadata(provider="mock", model="fixed", tool_name="fixed"),
        )


class AmbiguousMockAdapter(MockDicAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0
        self.reconcile_calls = 0

    async def execute_prepared(self, action: PreparedAction) -> object:  # type: ignore[override]
        del action
        self.execute_calls += 1
        raise DicAmbiguousWriteOutcomeError("synthetic timeout after submit")

    async def reconcile(self, action: PreparedAction) -> ReconciliationResult:
        self.reconcile_calls += 1
        return ReconciliationResult(
            action_id=action.action_id,
            state=ReconciliationState.CONFIRMED_NOT_APPLIED,
            detail="synthetic postcondition absent",
        )


class CapturingMockAdapter(MockDicAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.last_employee_query: EmployeeListQuery | None = None

    async def list_employees(self, query: EmployeeListQuery) -> EmployeeListResult:
        self.last_employee_query = query
        return await super().list_employees(query)


def actor(
    *roles: LogicalRole,
    entitlements: frozenset[str] = frozenset(),
    user_id: int = 1001,
) -> DiscordActor:
    return DiscordActor(
        user_id=user_id,
        guild_id=2001,
        channel_id=3001,
        logical_roles=frozenset(role.value for role in roles),
        discord_role_ids=frozenset({4001}),
        entitlements=entitlements,
    )


async def coordinator_for(
    router: FixedRouter,
    *,
    writes: bool = False,
    requester_actor_resolver: object | None = None,
    write_flags: frozenset[str] = frozenset({"ENABLE_EMPLOYEE_UPDATE"}),
    adapter_override: MockDicAdapter | None = None,
) -> tuple[BHApplicationCoordinator, MockDicAdapter, InMemoryApprovalRepository]:
    baseline = dict(DEFAULT_FEATURE_FLAGS)
    baseline["ENABLE_WRITE_ACTIONS"] = writes
    for flag in write_flags:
        baseline[flag] = writes
    flags = RuntimeFeatureFlags(baseline)
    adapter = adapter_override or MockDicAdapter()
    await adapter.ensure_authenticated()
    repository = InMemoryApprovalRepository()
    approvals = ApprovalService(
        repository,
        ConfirmationHasher(b"C" * 32),
        writes_enabled=lambda: flags.enabled("ENABLE_WRITE_ACTIONS"),
    )
    kwargs = {}
    if requester_actor_resolver is not None:
        kwargs["requester_actor_resolver"] = requester_actor_resolver
    coordinator = BHApplicationCoordinator(
        router=router,
        policy=PolicyEngine(),
        flags=flags,
        dic=DicService(adapter, flags),
        scope=ApplicationScope(
            allowed_guild_ids=frozenset({"2001"}),
            allowed_channel_ids=frozenset({"3001"}),
            current_tenant_id="TENANT-SYNTH-001",
            allowed_tenant_ids=frozenset({"TENANT-SYNTH-001"}),
        ),
        pseudonym_key=b"P" * 32,
        approvals=approvals,
        approval_repository=repository,
        payload_cipher=PayloadCipher(b"E" * 32),
        **kwargs,  # type: ignore[arg-type]
    )
    return coordinator, adapter, repository


@pytest.mark.asyncio
async def test_read_request_crosses_router_policy_and_mock_adapter() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="employee_count",
            function_id="EMP-READ-001",
            action_class=ActionClass.READ,
            employee_id=None,
            query=None,
            parameters={"status": "active"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.LOW,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router)
    try:
        result = await coordinator.ask(actor(LogicalRole.READ_ONLY), "Quanti dipendenti attivi?")
    finally:
        await adapter.close()

    assert result.success
    assert result.description == "Totale nel filtro richiesto: 1"
    assert router.exposed is not None
    assert "EMP-READ-001" in router.exposed
    assert "EMP-UPDATE-001" not in router.exposed


@pytest.mark.parametrize("sort_by", ["name", "payroll_number", "status", "contract"])
@pytest.mark.asyncio
async def test_employee_sort_parameters_reach_adapter_and_redacted_list_is_complete(
    sort_by: str,
) -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="sort_employees",
            function_id="EMP-SORT-001",
            action_class=ActionClass.FILTER,
            employee_id=None,
            query=None,
            parameters={"sort_by": sort_by, "sort_direction": "desc"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.MEDIUM,
            confidence=1.0,
        )
    )
    adapter = CapturingMockAdapter()
    coordinator, _, _ = await coordinator_for(router, adapter_override=adapter)
    try:
        result = await coordinator.ask(
            actor(LogicalRole.HR_READ), "Ordina per matricola decrescente"
        )
    finally:
        await adapter.close()

    assert adapter.last_employee_query is not None
    assert adapter.last_employee_query.sort_by == sort_by
    assert adapter.last_employee_query.sort_direction is SortDirection.DESC
    assert result.description == "Pagina 1; mostrati 1 su 1; pagina successiva: no"
    assert result.fields[0].name == "Employee ID EMP-SYNTH-001"
    rendered = result.fields[0].value
    for label in (
        "Stato account: connected",
        "Stato contratto: active",
        "Mansione: Synthetic tester",
        "Gruppo: Quality",
        "Luogo di lavoro: Synthetic office",
        "Matricola: ***-001",
    ):
        assert label in rendered
    assert "Matricola: SYN-001" not in rendered


@pytest.mark.parametrize(
    ("parameters", "error"),
    [
        ({"sort_by": 1}, "sort_by must be a supported string"),
        ({"sort_by": "unknown"}, "unsupported employee sort field"),
        ({"sort_direction": 1}, "sort_direction must be 'asc' or 'desc'"),
        ({"sort_direction": "sideways"}, "sort_direction must be 'asc' or 'desc'"),
    ],
)
@pytest.mark.asyncio
async def test_employee_sort_rejects_untyped_or_unsupported_parameters(
    parameters: dict[str, object], error: str
) -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="sort_employees",
            function_id="EMP-SORT-001",
            action_class=ActionClass.FILTER,
            employee_id=None,
            query=None,
            parameters=parameters,
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.MEDIUM,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router)
    try:
        with pytest.raises(ApplicationError, match=error):
            await coordinator.ask(actor(LogicalRole.HR_READ), "Ordinamento non valido")
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_payroll_read_renders_only_useful_minimized_metadata() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="payroll_metadata",
            function_id="EMP-PAY-001",
            action_class=ActionClass.READ,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"year": 2026},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router)
    try:
        result = await coordinator.ask(actor(LogicalRole.HR_READ), "Metadati busta paga 2026")
    finally:
        await adapter.close()

    assert result.description == "Record minimizzati: 1; mostrati: 1"
    assert result.fields[0].name == "Record 1"
    assert result.fields[0].value == (
        "Anno: 2026 · mese: 1 · stato: published · pubblicata: 2026-02-01"
    )
    assert "PAY-SYNTH-001" not in result.fields[0].value


@pytest.mark.asyncio
async def test_write_preview_does_not_mutate_and_confirmation_executes_once() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="employee_update",
            function_id="EMP-UPDATE-001",
            action_class=ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"job_title": "Synthetic Lead"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    coordinator, adapter, repository = await coordinator_for(router, writes=True)
    requester = actor(LogicalRole.HR_WRITE)
    before = await adapter.get_employee_summary("EMP-SYNTH-001")

    preview = await coordinator.ask(requester, "Modifica employee ID EMP-SYNTH-001")
    after_preview = await adapter.get_employee_summary("EMP-SYNTH-001")
    assert before.job_title == after_preview.job_title
    assert preview.action_id is not None
    match = re.search(r"Codice monouso: `([A-Z0-9]+)`", preview.description)
    assert match is not None

    completed = await coordinator.approve(requester, preview.action_id, match.group(1))
    after = await adapter.get_employee_summary("EMP-SYNTH-001")
    persisted = await repository.get(preview.action_id)
    await adapter.close()

    assert completed.success
    assert completed.description.endswith("SUCCEEDED")
    assert after.job_title == "Synthetic Lead"
    assert persisted is not None and persisted.status.value == "SUCCEEDED"


@pytest.mark.asyncio
async def test_execution_rechecks_requester_roles_instead_of_using_approver() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="employee_update",
            function_id="EMP-UPDATE-001",
            action_class=ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"job_title": "Must Not Apply"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )

    async def downgraded_requester(_action: object) -> DiscordActor:
        return actor(LogicalRole.HR_READ)

    coordinator, adapter, _ = await coordinator_for(
        router,
        writes=True,
        requester_actor_resolver=downgraded_requester,
    )
    requester = actor(LogicalRole.HR_WRITE)
    preview = await coordinator.ask(requester, "Modifica employee ID EMP-SYNTH-001")
    match = re.search(r"Codice monouso: `([A-Z0-9]+)`", preview.description)
    assert preview.action_id is not None and match is not None

    with pytest.raises(ApplicationPolicyDenied):
        await coordinator.approve(requester, preview.action_id, match.group(1))
    summary = await adapter.get_employee_summary("EMP-SYNTH-001")
    await adapter.close()
    assert summary.job_title != "Must Not Apply"


@pytest.mark.asyncio
async def test_requester_confirmation_then_distinct_approver_executes() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="contract_update",
            function_id="EMP-CONTRACT-002",
            action_class=ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"schedule": "Synthetic full time"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(
        router,
        writes=True,
        write_flags=frozenset({"ENABLE_CONTRACT_WRITE"}),
    )
    requester = actor(LogicalRole.HR_WRITE)
    preview = await coordinator.ask(requester, "contratto employee ID EMP-SYNTH-001")
    match = re.search(r"Codice monouso: `([A-Z0-9]+)`", preview.description)
    assert preview.action_id is not None and match is not None

    pending = await coordinator.approve(requester, preview.action_id, match.group(1))
    assert pending.action_id == preview.action_id
    completed = await coordinator.approve(
        actor(LogicalRole.APPROVER, user_id=1002),
        preview.action_id,
        "APPROVE",
    )
    contracts = await adapter.get_contracts("EMP-SYNTH-001")
    await adapter.close()

    assert completed.success
    assert any(contract.schedule == "Synthetic full time" for contract in contracts)


@pytest.mark.asyncio
async def test_ambiguous_write_is_reconciled_once_and_never_retried() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="employee_update",
            function_id="EMP-UPDATE-001",
            action_class=ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"job_title": "Uncertain"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    ambiguous_adapter = AmbiguousMockAdapter()
    coordinator, adapter, repository = await coordinator_for(
        router,
        writes=True,
        adapter_override=ambiguous_adapter,
    )
    requester = actor(LogicalRole.HR_WRITE)
    preview = await coordinator.ask(requester, "Modifica employee ID EMP-SYNTH-001")
    match = re.search(r"Codice monouso: `([A-Z0-9]+)`", preview.description)
    assert preview.action_id is not None and match is not None

    result = await coordinator.approve(requester, preview.action_id, match.group(1))
    persisted = await repository.get(preview.action_id)
    await adapter.close()

    assert not result.success
    assert persisted is not None
    assert persisted.status.value == "RECONCILED_NOT_APPLIED"
    assert ambiguous_adapter.execute_calls == 1
    assert ambiguous_adapter.reconcile_calls == 1


@pytest.mark.asyncio
async def test_contract_query_without_employee_id_is_allowed_and_paginated() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="contracts",
            function_id="EMP-CONTRACT-001",
            action_class=ActionClass.READ,
            employee_id=None,
            query=None,
            parameters={},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.MEDIUM,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router)
    try:
        result = await coordinator.contracts(actor(LogicalRole.HR_READ), None, None, None)
    finally:
        await adapter.close()
    assert result.success
    assert result.fields


@pytest.mark.parametrize(
    ("function_id", "logical_role", "employee_id", "parameters", "expected_title"),
    [
        ("EMP-READ-001", LogicalRole.HR_READ, None, {}, "Dipendenti"),
        ("EMP-SEARCH-001", LogicalRole.HR_READ, None, {}, "Dipendenti"),
        ("EMP-FILTER-001", LogicalRole.HR_READ, None, {"status": "all"}, "Dipendenti"),
        ("EMP-SORT-001", LogicalRole.HR_READ, None, {}, "Dipendenti"),
        ("EMP-PAGE-001", LogicalRole.HR_READ, None, {"page": 1}, "Dipendenti"),
        (
            "EMP-READ-002",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {},
            "Dipendente EMP-SYNTH-001",
        ),
        (
            "EMP-RBAC-001",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {},
            "Ruoli EMP-SYNTH-001",
        ),
        (
            "EMP-TIME-001",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {},
            "Timbratura EMP-SYNTH-001",
        ),
        (
            "EMP-MAT-001",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {},
            "Maturazioni EMP-SYNTH-001",
        ),
        (
            "EMP-BAL-001",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {"year": 2026},
            "Bilancio EMP-SYNTH-001",
        ),
        (
            "EMP-PAY-001",
            LogicalRole.HR_READ,
            "EMP-SYNTH-001",
            {"year": 2026},
            "Metadati buste paga EMP-SYNTH-001",
        ),
        (
            "EMP-DOC-001",
            LogicalRole.DOCUMENT_OPERATOR,
            "EMP-SYNTH-001",
            {"status": "uploaded"},
            "Metadati documenti EMP-SYNTH-001",
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_function_matrix_dispatches_deterministically(
    function_id: str,
    logical_role: LogicalRole,
    employee_id: str | None,
    parameters: dict[str, object],
    expected_title: str,
) -> None:
    action_class = (
        ActionClass.SEARCH
        if function_id == "EMP-SEARCH-001"
        else ActionClass.FILTER
        if function_id in {"EMP-FILTER-001", "EMP-SORT-001", "EMP-PAGE-001"}
        else ActionClass.READ
    )
    router = FixedRouter(
        IntentEnvelope(
            intent="matrix_read",
            function_id=function_id,
            action_class=action_class,
            employee_id=employee_id,
            query="synthetic" if function_id == "EMP-SEARCH-001" else None,
            parameters=parameters,
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router)
    entitlements = frozenset({"balances:read"}) if function_id == "EMP-BAL-001" else frozenset()
    try:
        result = await coordinator.ask(
            actor(logical_role, entitlements=entitlements), "elenco sintetico"
        )
    finally:
        await adapter.close()
    assert result.success
    assert expected_title in result.title


@pytest.mark.asyncio
async def test_application_status_help_pending_reject_and_denials() -> None:
    router = FixedRouter(
        IntentEnvelope(
            intent="employee_update",
            function_id="EMP-UPDATE-001",
            action_class=ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            query=None,
            parameters={"job_title": "Rejected Preview"},
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=Sensitivity.HIGH,
            confidence=1.0,
        )
    )
    coordinator, adapter, _ = await coordinator_for(router, writes=True)
    requester = actor(LogicalRole.HR_WRITE)
    try:
        assert "EMP-READ-001" in (await coordinator.help(requester)).description
        assert "ENABLED" in (await coordinator.status(requester)).fields[-1].value
        assert (await coordinator.health(requester)).success
        assert (await coordinator.pending(requester)).description == "Totale visibile: 0"

        preview = await coordinator.ask(requester, "Modifica employee ID EMP-SYNTH-001")
        assert preview.action_id is not None
        assert (await coordinator.pending(requester)).fields
        rejected = await coordinator.reject(requester, preview.action_id, "Richiesta annullata")
        assert rejected.success

        with pytest.raises(ApplicationPolicyDenied):
            await coordinator.employee(actor(LogicalRole.READ_ONLY), "EMP-SYNTH-001")
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_clarification_unsupported_and_write_precondition_responses() -> None:
    clarification = IntentEnvelope(
        intent="unsupported",
        function_id="UNSUPPORTED",
        action_class=ActionClass.UNSUPPORTED,
        employee_id=None,
        query=None,
        parameters={},
        date_from=None,
        date_to=None,
        requires_clarification=True,
        clarification_question="Specifica il target.",
        sensitivity=Sensitivity.LOW,
        confidence=0.0,
    )
    coordinator, adapter, _ = await coordinator_for(FixedRouter(clarification))
    assert not (await coordinator.ask(actor(LogicalRole.HR_READ), "ambigua")).success
    await adapter.close()

    missing_target = IntentEnvelope(
        intent="update",
        function_id="EMP-UPDATE-001",
        action_class=ActionClass.PREPARE_WRITE,
        employee_id=None,
        query=None,
        parameters={"job_title": "Synthetic"},
        date_from=None,
        date_to=None,
        requires_clarification=False,
        clarification_question=None,
        sensitivity=Sensitivity.HIGH,
        confidence=1.0,
    )
    coordinator, adapter, _ = await coordinator_for(FixedRouter(missing_target), writes=True)
    with pytest.raises(ApplicationPolicyDenied, match="employee ID is required"):
        await coordinator.ask(actor(LogicalRole.HR_WRITE), "modifica")
    await adapter.close()
