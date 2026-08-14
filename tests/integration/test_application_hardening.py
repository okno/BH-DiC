from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine import URL

import bh_dic.application as application_module
from bh_dic.application import (
    ApplicationError,
    ApplicationPolicyDenied,
    ApplicationScope,
    BHApplicationCoordinator,
)
from bh_dic.approvals.confirmation import ConfirmationHasher
from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.approvals.service import ApprovalService
from bh_dic.approvals.storage import InMemoryApprovalRepository
from bh_dic.audit.service import AuditService
from bh_dic.database.engine import Database
from bh_dic.dic.errors import DicAmbiguousWriteOutcomeError
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import (
    ContractRecord,
    EmployeeListResult,
    HealthStatus,
    ReconciliationResult,
    ReconciliationState,
)
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import AttachmentPayload
from bh_dic.files.models import UploadRecord, UploadStatus
from bh_dic.files.service import FileService
from bh_dic.openai.client import RoutedIntent
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, RouteMetadata, Sensitivity
from bh_dic.policies.engine import PolicyEngine
from bh_dic.policies.feature_flags import DEFAULT_FEATURE_FLAGS, RuntimeFeatureFlags
from bh_dic.policies.roles import LogicalRole
from bh_dic.security.cipher import PayloadCipher
from bh_dic.services.dic_service import DicService


class FixedRouter:
    def __init__(self, envelope: IntentEnvelope) -> None:
        self.envelope = envelope

    async def route(self, request: str, allowed_function_ids: frozenset[str]) -> RoutedIntent:
        del request, allowed_function_ids
        return RoutedIntent(
            envelope=self.envelope,
            metadata=RouteMetadata(provider="mock", model="fixed", tool_name="fixed"),
        )


def _intent(
    function_id: str,
    action_class: ActionClass,
    *,
    employee_id: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    requires_clarification: bool = False,
    clarification_question: str | None = None,
) -> IntentEnvelope:
    return IntentEnvelope(
        intent="synthetic_intent",
        function_id=function_id,
        action_class=action_class,
        employee_id=employee_id,
        query=None,
        parameters=dict(parameters or {}),
        date_from=None,
        date_to=None,
        requires_clarification=requires_clarification,
        clarification_question=clarification_question,
        sensitivity=(Sensitivity.LOW if action_class is ActionClass.READ else Sensitivity.HIGH),
        confidence=1.0,
    )


def _actor(
    *roles: LogicalRole,
    user_id: int = 1001,
    entitlements: frozenset[str] = frozenset(),
) -> DiscordActor:
    return DiscordActor(
        user_id=user_id,
        guild_id=2001,
        channel_id=3001,
        logical_roles=frozenset(role.value for role in roles),
        discord_role_ids=frozenset({4001}),
        entitlements=entitlements,
    )


@dataclass(slots=True)
class Harness:
    coordinator: BHApplicationCoordinator
    adapter: MockDicAdapter
    approvals: ApprovalService | None
    repository: InMemoryApprovalRepository | None
    cipher: PayloadCipher | None
    flags: RuntimeFeatureFlags

    async def close(self) -> None:
        await self.adapter.close()


async def _harness(
    envelope: IntentEnvelope | None = None,
    *,
    writes: bool = False,
    write_flags: frozenset[str] = frozenset(),
    workflows: bool = True,
    files: object | None = None,
    audit: AuditService | None = None,
    capabilities: frozenset[str] = frozenset(),
) -> Harness:
    baseline = dict(DEFAULT_FEATURE_FLAGS)
    baseline["ENABLE_WRITE_ACTIONS"] = writes
    for flag in write_flags:
        baseline[flag] = writes
    flags = RuntimeFeatureFlags(baseline)
    adapter = MockDicAdapter()
    await adapter.ensure_authenticated()
    repository = InMemoryApprovalRepository() if workflows else None
    cipher = PayloadCipher(b"E" * 32) if workflows else None
    approvals = (
        ApprovalService(
            repository,
            ConfirmationHasher(b"C" * 32),
            writes_enabled=lambda: flags.enabled("ENABLE_WRITE_ACTIONS"),
        )
        if repository is not None
        else None
    )
    router = FixedRouter(
        envelope
        or _intent(
            "UNSUPPORTED",
            ActionClass.UNSUPPORTED,
        )
    )
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
            capabilities=capabilities,
        ),
        pseudonym_key=b"P" * 32,
        audit=audit,
        approvals=approvals,
        approval_repository=repository,
        payload_cipher=cipher,
        files=cast(FileService | None, files),
    )
    return Harness(coordinator, adapter, approvals, repository, cipher, flags)


def _confirmation_code(description: str) -> str:
    match = re.search(r"Codice monouso: `([A-Z0-9]+)`", description)
    assert match is not None
    return match.group(1)


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(hide_password=False)


class FileStub:
    def __init__(self, status: UploadStatus) -> None:
        self.status = status
        self.received = b""

    async def ingest(
        self,
        *,
        original_filename: str,
        claimed_mime: str | None,
        chunks: AsyncIterator[bytes],
    ) -> UploadRecord:
        self.received = b"".join([chunk async for chunk in chunks])
        now = datetime.now(UTC)
        return UploadRecord(
            upload_id="00000000-0000-4000-8000-000000000501",
            original_filename=original_filename,
            opaque_name="00000000-0000-4000-8000-000000000501.bin",
            status=self.status,
            bucket="quarantine",
            claimed_mime=claimed_mime,
            detected_mime="application/pdf",
            size_bytes=len(self.received),
            sha256="a" * 64,
            antivirus_status="CLEAN" if self.status is UploadStatus.CLEAN else "REJECTED",
            rejection_reason=None if self.status is UploadStatus.CLEAN else "synthetic rejection",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )


def test_coordinator_rejects_short_pseudonym_key() -> None:
    adapter = MockDicAdapter()
    flags = RuntimeFeatureFlags()
    with pytest.raises(ValueError, match="pseudonym key"):
        BHApplicationCoordinator(
            router=FixedRouter(_intent("UNSUPPORTED", ActionClass.UNSUPPORTED)),
            policy=PolicyEngine(),
            flags=flags,
            dic=DicService(adapter, flags),
            scope=ApplicationScope(
                allowed_guild_ids=frozenset({"2001"}),
                allowed_channel_ids=frozenset({"3001"}),
                current_tenant_id="TENANT-SYNTH-001",
                allowed_tenant_ids=frozenset({"TENANT-SYNTH-001"}),
            ),
            pseudonym_key=b"short",
        )


@pytest.mark.asyncio
async def test_unconfigured_workflow_components_fail_closed() -> None:
    harness = await _harness(writes=True, workflows=False)
    actor = _actor(LogicalRole.HR_WRITE)
    try:
        assert not (await harness.coordinator.pending(actor)).success
        assert not (await harness.coordinator.approve(actor, "missing", "CODE")).success
        assert not (await harness.coordinator.reject(actor, "missing", "reason")).success
        with pytest.raises(ApplicationError, match="dependencies are missing"):
            await harness.coordinator._execute_approved(
                actor,
                cast(PendingAction, object()),
            )
        write = await harness.coordinator._prepare_write(
            actor,
            "corr-unavailable-write",
            _intent(
                "EMP-UPDATE-001",
                ActionClass.PREPARE_WRITE,
                employee_id="EMP-SYNTH-001",
                parameters={"job_title": "Synthetic"},
            ),
        )
        assert not write.success
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_unsupported_help_and_degraded_health_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await _harness()
    try:
        unsupported = await harness.coordinator.ask(_actor(LogicalRole.HR_READ), "non supportata")
        assert unsupported.title == "Funzione non disponibile"
        assert not unsupported.success

        help_result = await harness.coordinator.help(_actor())
        assert help_result.description == "Nessuna funzione disponibile per i ruoli correnti."

        degraded = HealthStatus(
            ready=False,
            authenticated=False,
            browser_available=True,
            detail="synthetic degraded",
        )
        monkeypatch.setattr(harness.adapter, "health", AsyncMock(return_value=degraded))
        status = await harness.coordinator.status(_actor())
        health = await harness.coordinator.health(_actor())
        assert status.fields[0].value == "DEGRADED"
        assert status.fields[1].value == "available"
        assert status.fields[2].value == "DISABLED"
        assert health.description == "synthetic degraded"
        assert not health.success
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_approver_must_use_explicit_approve_literal() -> None:
    harness = await _harness(
        _intent(
            "EMP-CONTRACT-002",
            ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            parameters={"schedule": "Synthetic"},
        ),
        writes=True,
        write_flags=frozenset({"ENABLE_CONTRACT_WRITE"}),
    )
    requester = _actor(LogicalRole.HR_WRITE)
    try:
        preview = await harness.coordinator.ask(requester, "contratto sintetico")
        assert preview.action_id is not None
        await harness.coordinator.approve(
            requester, preview.action_id, _confirmation_code(preview.description)
        )
        with pytest.raises(ApplicationError, match="APPROVE"):
            await harness.coordinator.approve(
                _actor(LogicalRole.APPROVER, user_id=1002),
                preview.action_id,
                "yes",
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_upload_denial_unavailable_size_and_scan_outcomes() -> None:
    attachment = AttachmentPayload(
        original_filename="synthetic.pdf",
        content_type="application/pdf",
        declared_size=4,
        content=b"data",
    )
    flags = frozenset({"ENABLE_DOCUMENT_UPLOAD"})

    missing = await _harness(
        writes=True,
        write_flags=flags,
        workflows=True,
        capabilities=frozenset({"clamav"}),
    )
    try:
        with pytest.raises(ApplicationPolicyDenied):
            await missing.coordinator.upload(
                _actor(LogicalRole.HR_READ), "EMP-SYNTH-001", "CV", attachment
            )
        unavailable = await missing.coordinator.upload(
            _actor(LogicalRole.DOCUMENT_OPERATOR), "EMP-SYNTH-001", "CV", attachment
        )
        assert not unavailable.success
    finally:
        await missing.close()

    rejected_files = FileStub(UploadStatus.REJECTED)
    rejected = await _harness(
        writes=True,
        write_flags=flags,
        files=rejected_files,
        capabilities=frozenset({"clamav"}),
    )
    try:
        with pytest.raises(ApplicationError, match="dimensione"):
            await rejected.coordinator.upload(
                _actor(LogicalRole.DOCUMENT_OPERATOR),
                "EMP-SYNTH-001",
                "CV",
                replace(attachment, declared_size=99),
            )
        denied = await rejected.coordinator.upload(
            _actor(LogicalRole.DOCUMENT_OPERATOR), "EMP-SYNTH-001", "CV", attachment
        )
        assert rejected_files.received == b"data"
        assert denied.title == "Allegato rifiutato"
        assert not denied.success
    finally:
        await rejected.close()

    clean_files = FileStub(UploadStatus.CLEAN)
    clean = await _harness(
        writes=True,
        write_flags=flags,
        files=clean_files,
        capabilities=frozenset({"clamav"}),
    )
    try:
        preview = await clean.coordinator.upload(
            _actor(LogicalRole.DOCUMENT_OPERATOR), "EMP-SYNTH-001", "CV", attachment
        )
        assert preview.action_id is not None
        assert preview.title == "Anteprima EMP-DOC-002"
    finally:
        await clean.close()


@pytest.mark.asyncio
async def test_prepare_write_validates_target_parameters_motivation_and_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _harness(writes=True)
    actor = _actor(LogicalRole.HR_WRITE)
    try:
        missing_target = await harness.coordinator._prepare_write(
            actor,
            "corr-missing-target",
            _intent(
                "EMP-UPDATE-001",
                ActionClass.PREPARE_WRITE,
                parameters={"job_title": "Synthetic"},
            ),
        )
        assert missing_target.title == "Employee ID necessario"

        missing_parameters = await harness.coordinator._prepare_write(
            actor,
            "corr-missing-parameters",
            _intent(
                "EMP-UPDATE-001",
                ActionClass.PREPARE_WRITE,
                employee_id="EMP-SYNTH-001",
            ),
        )
        assert missing_parameters.title == "Parametri mancanti"

        no_motivation = await harness.coordinator._prepare_write(
            actor,
            "corr-missing-motivation",
            _intent(
                "EMP-BAL-002",
                ActionClass.PREPARE_WRITE,
                employee_id="EMP-SYNTH-001",
                parameters={"year": 2026, "amount": "1"},
            ),
        )
        assert no_motivation.title == "Motivazione necessaria"

        critical = await harness.coordinator._prepare_write(
            actor,
            "corr-critical-preview",
            _intent(
                "EMP-BAL-002",
                ActionClass.PREPARE_WRITE,
                employee_id="EMP-SYNTH-001",
                parameters={"year": 2026, "amount": "1", "motivation": " Synthetic reason "},
            ),
        )
        assert "CONFIRM EMP-SYNTH-001" in critical.description
        assert critical.action_id is not None

        targetless = await harness.coordinator._prepare_write(
            actor,
            "corr-targetless",
            _intent(
                "EMP-CREATE-001",
                ActionClass.PREPARE_WRITE,
                parameters={"display_name_redacted": "N. E."},
            ),
        )
        assert targetless.fields[0].value.startswith("Nuovo record")

        monkeypatch.setattr(application_module, "redact_structure", lambda _value: [])
        with pytest.raises(ApplicationError, match="remain an object"):
            await harness.coordinator._prepare_write(
                actor,
                "corr-redaction-shape",
                _intent(
                    "EMP-UPDATE-001",
                    ActionClass.PREPARE_WRITE,
                    employee_id="EMP-SYNTH-001",
                    parameters={"job_title": "Synthetic"},
                ),
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_decrypted_pending_payload_validation_is_fail_closed() -> None:
    harness = await _harness(writes=True)
    assert harness.repository is not None
    assert harness.cipher is not None
    try:
        preview = await harness.coordinator._prepare_write(
            _actor(LogicalRole.HR_WRITE),
            "corr-payload-validation",
            _intent(
                "EMP-UPDATE-001",
                ActionClass.PREPARE_WRITE,
                employee_id="EMP-SYNTH-001",
                parameters={"job_title": "Synthetic"},
            ),
        )
        assert preview.action_id is not None
        action = await harness.repository.get(preview.action_id)
        assert action is not None

        payloads_and_errors: list[tuple[object, str]] = [
            ([], "invalid version"),
            ({"version": 2}, "invalid version"),
            (
                {"version": 1, "parameters": [], "requester_context": {}},
                "malformed",
            ),
            (
                {
                    "version": 1,
                    "parameters": {},
                    "requester_context": {"logical_roles": "HR_WRITE", "entitlements": []},
                },
                "authorization is malformed",
            ),
            (
                {
                    "version": 1,
                    "parameters": {},
                    "requester_context": {
                        "user_id": "not-an-integer",
                        "guild_id": "2001",
                        "channel_id": "3001",
                        "logical_roles": [],
                        "entitlements": [],
                    },
                },
                "identity is malformed",
            ),
            (
                {
                    "version": 1,
                    "parameters": {},
                    "requester_context": {
                        "user_id": "9999",
                        "guild_id": "2001",
                        "channel_id": "3001",
                        "logical_roles": [],
                        "entitlements": [],
                    },
                },
                "does not match pending action",
            ),
        ]
        for payload, message in payloads_and_errors:
            malformed = replace(
                action,
                encrypted_parameters=harness.cipher.encrypt_json(payload),
            )
            with pytest.raises(ApplicationError, match=message):
                harness.coordinator._decode_pending_payload(malformed)

        harness.coordinator.payload_cipher = None
        with pytest.raises(ApplicationError, match="cipher is unavailable"):
            harness.coordinator._decode_pending_payload(action)
    finally:
        await harness.close()


@pytest.mark.parametrize(
    ("reconciliation", "expected_status", "success"),
    [
        (ReconciliationState.CONFIRMED_APPLIED, ActionStatus.SUCCEEDED, True),
        (
            ReconciliationState.CONFIRMED_NOT_APPLIED,
            ActionStatus.RECONCILED_NOT_APPLIED,
            False,
        ),
        (ReconciliationState.UNKNOWN, ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION, False),
        (
            RuntimeError("synthetic reconciliation failure"),
            ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION,
            False,
        ),
    ],
)
@pytest.mark.asyncio
async def test_uncertain_write_reconciles_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    reconciliation: ReconciliationState | Exception,
    expected_status: ActionStatus,
    success: bool,
) -> None:
    harness = await _harness(
        _intent(
            "EMP-UPDATE-001",
            ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            parameters={"job_title": "Synthetic"},
        ),
        writes=True,
        write_flags=frozenset({"ENABLE_EMPLOYEE_UPDATE"}),
    )
    assert harness.repository is not None
    requester = _actor(LogicalRole.HR_WRITE)
    try:
        preview = await harness.coordinator.ask(requester, "modifica sintetica")
        assert preview.action_id is not None
        execute = AsyncMock(side_effect=DicAmbiguousWriteOutcomeError())
        monkeypatch.setattr(harness.coordinator.dic, "execute", execute)
        if isinstance(reconciliation, Exception):
            reconcile = AsyncMock(side_effect=reconciliation)
        else:
            reconcile = AsyncMock(
                return_value=ReconciliationResult(
                    action_id=preview.action_id,
                    state=reconciliation,
                    detail="synthetic reconciliation",
                )
            )
        monkeypatch.setattr(harness.coordinator.dic, "reconcile", reconcile)

        result = await harness.coordinator.approve(
            requester,
            preview.action_id,
            _confirmation_code(preview.description),
        )
        persisted = await harness.repository.get(preview.action_id)
        assert persisted is not None
        assert persisted.status is expected_status
        assert result.success is success
        execute.assert_awaited_once()
        reconcile.assert_awaited_once()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_deterministic_execution_failure_is_persisted_and_reraised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _harness(
        _intent(
            "EMP-UPDATE-001",
            ActionClass.PREPARE_WRITE,
            employee_id="EMP-SYNTH-001",
            parameters={"job_title": "Synthetic"},
        ),
        writes=True,
        write_flags=frozenset({"ENABLE_EMPLOYEE_UPDATE"}),
    )
    assert harness.repository is not None
    requester = _actor(LogicalRole.HR_WRITE)
    try:
        preview = await harness.coordinator.ask(requester, "modifica sintetica")
        assert preview.action_id is not None
        monkeypatch.setattr(
            harness.coordinator.dic,
            "execute",
            AsyncMock(side_effect=RuntimeError("synthetic deterministic failure")),
        )
        with pytest.raises(RuntimeError, match="deterministic failure"):
            await harness.coordinator.approve(
                requester,
                preview.action_id,
                _confirmation_code(preview.description),
            )
        persisted = await harness.repository.get(preview.action_id)
        assert persisted is not None
        assert persisted.status is ActionStatus.FAILED
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_application_audits_success_clarification_and_policy_denial(tmp_path: Path) -> None:
    database = Database(_sqlite_url(tmp_path / "application-audit.sqlite3"))
    await database.create_schema()
    audit = AuditService(database, b"A" * 32)
    envelope = _intent(
        "EMP-READ-002",
        ActionClass.READ,
        employee_id="EMP-SYNTH-001",
    )
    harness = await _harness(envelope, audit=audit)
    router = cast(FixedRouter, harness.coordinator.router)
    try:
        result = await harness.coordinator.ask(_actor(LogicalRole.HR_READ), "riepilogo")
        assert result.success

        router.envelope = _intent(
            "EMP-READ-002",
            ActionClass.READ,
            employee_id="EMP-SYNTH-001",
            requires_clarification=True,
            clarification_question="Specifica il campo.",
        )
        clarification = await harness.coordinator.ask(_actor(LogicalRole.HR_READ), "ambigua")
        assert not clarification.success

        router.envelope = envelope
        with pytest.raises(ApplicationPolicyDenied):
            await harness.coordinator.ask(_actor(LogicalRole.READ_ONLY), "riepilogo vietato")

        assert await audit.count() == 3
        assert (await audit.verify_or_raise()).valid
    finally:
        await harness.close()
        await database.dispose()


@pytest.mark.asyncio
async def test_contract_rendering_filters_invalid_and_out_of_range_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _harness()
    contracts = (
        ContractRecord(
            contract_id="CON-INVALID",
            employee_id="EMP-SYNTH-001",
            end_date="not-a-date",
            contract_type="invalid",
        ),
        ContractRecord(
            contract_id="CON-BEFORE",
            employee_id="EMP-SYNTH-001",
            end_date="2026-07-31",
            contract_type="before",
        ),
        ContractRecord(
            contract_id="CON-IN-RANGE",
            employee_id="EMP-SYNTH-001",
            end_date="2026-08-15",
            contract_type="included",
        ),
        ContractRecord(
            contract_id="CON-AFTER",
            employee_id="EMP-SYNTH-001",
            end_date="2026-09-01",
            contract_type="after",
        ),
    )
    monkeypatch.setattr(
        harness.coordinator.dic,
        "get_contracts",
        AsyncMock(return_value=contracts),
    )
    intent = BHApplicationCoordinator._direct_intent(
        "EMP-CONTRACT-001",
        employee_id="EMP-SYNTH-001",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
    )
    try:
        result = await harness.coordinator._render_contracts(intent, "corr-contract-filter")
        assert len(result.fields) == 1
        assert "included" in result.fields[0].value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_direct_document_pending_and_balance_commands() -> None:
    harness = await _harness()
    try:
        documents = await harness.coordinator.documents(
            _actor(LogicalRole.DOCUMENT_OPERATOR),
            "EMP-SYNTH-001",
            "pending",
        )
        all_documents = await harness.coordinator.documents(
            _actor(LogicalRole.DOCUMENT_OPERATOR),
            "EMP-SYNTH-001",
            None,
        )
        balance = await harness.coordinator.balances(
            _actor(
                LogicalRole.HR_READ,
                entitlements=frozenset({"balances:read"}),
            ),
            "EMP-SYNTH-001",
            2026,
        )
        assert documents.title.startswith("Metadati documenti")
        assert all_documents.title.startswith("Metadati documenti")
        assert balance.title.startswith("Bilancio")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_contract_employee_pagination_has_a_hard_safety_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _harness()
    never_ending_page = EmployeeListResult(
        items=(),
        page=1,
        page_size=100,
        total=0,
        has_next=True,
    )
    list_employees = AsyncMock(return_value=never_ending_page)
    monkeypatch.setattr(harness.coordinator.dic, "list_employees", list_employees)
    try:
        with pytest.raises(ApplicationError, match="pagination safety limit"):
            await harness.coordinator._render_contracts(
                BHApplicationCoordinator._direct_intent("EMP-CONTRACT-001"),
                "corr-pagination-limit",
            )
        assert list_employees.await_count == 100
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_static_application_guards_and_missing_dispatcher() -> None:
    harness = await _harness()
    try:
        assert (
            BHApplicationCoordinator._operation_scope(
                _intent("EMP-READ-001", ActionClass.READ), "Numero dipendenti"
            )
            == "aggregate"
        )
        assert (
            BHApplicationCoordinator._operation_scope(
                _intent("EMP-READ-001", ActionClass.READ), "Elenca dipendenti"
            )
            == "default"
        )
        with pytest.raises(ApplicationError, match="employee_id"):
            BHApplicationCoordinator._require_employee(_intent("EMP-READ-002", ActionClass.READ))
        with pytest.raises(ApplicationError, match="unknown Function ID"):
            BHApplicationCoordinator._spec("EMP-NOT-REAL")
        with pytest.raises(ApplicationError, match="dispatcher missing"):
            await harness.coordinator._dispatch_read(
                _actor(LogicalRole.HR_WRITE),
                "corr-missing-dispatch",
                _intent(
                    "EMP-UPDATE-001",
                    ActionClass.PREPARE_WRITE,
                    employee_id="EMP-SYNTH-001",
                    parameters={"job_title": "Synthetic"},
                ),
                "default",
            )
    finally:
        await harness.close()
