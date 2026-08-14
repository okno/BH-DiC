from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import JsonValue, SecretStr

from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicAuthenticationError,
    DicAuthorizationError,
    DicReconciliationRequiredError,
    DicValidationError,
    DicWriteDisabledError,
)
from bh_dic.dic.models import (
    AccountState,
    BalanceResult,
    ContractRecord,
    DicCredentials,
    DocumentMetadata,
    DocumentQuery,
    EmployeeListItem,
    EmployeeListQuery,
    EmployeeListResult,
    EmployeeState,
    EmployeeSummary,
    FunctionId,
    MaturationRecord,
    PayrollMetadata,
    PreparedAction,
    ReconciliationResult,
    ReconciliationState,
    RolesResult,
    SessionState,
    SessionStatus,
    TimeAccessResult,
)
from bh_dic.dic.playwright_adapter import PlaywrightDicAdapter


class UnusedSyntheticPage:
    """Page Objects are replaced before any synthetic adapter operation reaches the DOM."""


class DirectCoordinator:
    def __init__(self) -> None:
        self.write_error: Exception | None = None
        self.closed = False

    async def run_read(self, name, lock_key, operation):
        del name, lock_key
        return await operation()

    async def run_write(self, name, lock_key, operation):
        del name, lock_key
        if self.write_error is not None:
            raise self.write_error
        return await operation()

    async def run_reconciliation(self, lock_key, operation):
        del lock_key
        return await operation()

    async def close(self) -> None:
        self.closed = True


def _action(
    function_id: FunctionId,
    parameters: dict[str, JsonValue] | None = None,
    *,
    employee_id: str | None = "EMP-SYNTH-001",
) -> PreparedAction:
    now = datetime.now(UTC)
    return PreparedAction(
        action_id=str(uuid4()),
        function_id=function_id,
        employee_id=employee_id,
        parameters=parameters or {},
        idempotency_key="idem-synthetic-001",  # gitleaks:allow -- synthetic fixture
        correlation_id="corr-synthetic-001",
        request_fingerprint="b" * 64,
        preview=(),
        required_approvals=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _adapter(
    *,
    coordinator: DirectCoordinator | None = None,
    live_writes_enabled: bool = False,
    quarantine_root: Path | None = None,
) -> tuple[PlaywrightDicAdapter, DirectCoordinator]:
    direct = coordinator or DirectCoordinator()
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=direct,  # type: ignore[arg-type]
        expected_tenant_id="TENANT-SYNTH-001",
        quarantine_root=quarantine_root,
        live_writes_enabled=live_writes_enabled,
    )
    return adapter, direct


def _auth(status: SessionState = SessionState.AUTHENTICATED) -> SimpleNamespace:
    return SimpleNamespace(
        status=AsyncMock(return_value=SessionStatus(state=status)),
        authenticate=AsyncMock(return_value=SessionStatus(state=SessionState.AUTHENTICATED)),
    )


@pytest.mark.asyncio
async def test_health_authentication_and_close_are_fail_closed() -> None:
    adapter, coordinator = _adapter()
    adapter._auth = _auth()
    health = await adapter.health()
    assert health.ready is True
    assert health.authenticated is True

    adapter._auth.status = AsyncMock(side_effect=DicAuthorizationError("synthetic tenant mismatch"))
    unavailable = await adapter.health()
    assert unavailable.ready is True
    assert unavailable.authenticated is False

    adapter._auth = _auth(SessionState.UNKNOWN)
    with pytest.raises(DicAuthenticationError, match="credentials are required"):
        await adapter.ensure_authenticated()
    credentials = DicCredentials(username="synthetic", password=SecretStr("synthetic-password"))
    assert (await adapter.ensure_authenticated(credentials)).state is SessionState.AUTHENTICATED
    adapter._auth.authenticate.assert_awaited_once_with(credentials)

    adapter._auth = _auth()
    assert (await adapter.ensure_authenticated()).state is SessionState.AUTHENTICATED
    await adapter.close()
    await adapter.close()
    assert coordinator.closed is True
    assert (await adapter.health()).ready is False
    with pytest.raises(DicValidationError, match="closed"):
        await adapter.session_status()


@pytest.mark.asyncio
async def test_read_protocol_delegates_only_after_tenant_bound_authentication() -> None:
    adapter, _ = _adapter()
    adapter._auth = _auth()
    employee_id = "EMP-SYNTH-001"
    employee_list = EmployeeListResult(items=(), page=1, page_size=25, total=0, has_next=False)
    summary = EmployeeSummary(employee_id=employee_id)
    contracts = (ContractRecord(contract_id="CON-SYNTH-001", employee_id=employee_id),)
    roles = RolesResult(employee_id=employee_id)
    maturations = (
        MaturationRecord(maturation_id="MAT-SYNTH-001", employee_id=employee_id, category="Ferie"),
    )
    balance = BalanceResult(employee_id=employee_id, year=2026, lines=())
    payrolls = (PayrollMetadata(payroll_id="PAY-SYNTH-001", employee_id=employee_id, year=2026),)
    documents = (
        DocumentMetadata(
            document_id="DOC-SYNTH-001",
            employee_id=employee_id,
            title_redacted="[REDACTED]",
        ),
    )
    adapter._employees = SimpleNamespace(list=AsyncMock(return_value=employee_list))
    adapter._summary = SimpleNamespace(read=AsyncMock(return_value=summary))
    adapter._contracts = SimpleNamespace(read=AsyncMock(return_value=contracts))
    adapter._roles = SimpleNamespace(
        read_roles=AsyncMock(return_value=roles),
        read_time_access=AsyncMock(return_value=TimeAccessResult(employee_id=employee_id)),
    )
    adapter._timestamps = SimpleNamespace(read_enabled=AsyncMock(return_value=True))
    adapter._maturations = SimpleNamespace(read=AsyncMock(return_value=maturations))
    adapter._balance = SimpleNamespace(read=AsyncMock(return_value=balance))
    adapter._payrolls = SimpleNamespace(read=AsyncMock(return_value=payrolls))
    adapter._documents = SimpleNamespace(read=AsyncMock(return_value=documents))

    query = EmployeeListQuery()
    document_query = DocumentQuery()
    assert await adapter.list_employees(query) == employee_list
    assert await adapter.get_employee_summary(employee_id) == summary
    assert await adapter.get_contracts(employee_id) == contracts
    assert await adapter.get_roles(employee_id) == roles
    assert (await adapter.get_time_access(employee_id)).timestamping_enabled is True
    assert await adapter.get_maturations(employee_id) == maturations
    assert await adapter.get_balance(employee_id, 2026) == balance
    assert await adapter.get_payroll_metadata(employee_id, 2026) == payrolls
    assert await adapter.get_document_metadata(employee_id, document_query) == documents

    adapter._roles.read_time_access = AsyncMock(
        return_value=TimeAccessResult(employee_id=employee_id, timestamping_enabled=False)
    )
    assert (await adapter.get_time_access(employee_id)).timestamping_enabled is False
    adapter._timestamps.read_enabled.assert_awaited_once()

    adapter._auth = _auth(SessionState.UNKNOWN)
    with pytest.raises(DicAuthenticationError, match="tenant-bound"):
        await adapter.list_employees(query)


@pytest.mark.asyncio
async def test_dispatch_write_routes_every_supported_family_and_blocks_artifacts(
    monkeypatch,
) -> None:
    adapter, _ = _adapter()
    adapter._employees = SimpleNamespace(create_employee=AsyncMock())
    adapter._summary = SimpleNamespace(execute=AsyncMock())
    adapter._contracts = SimpleNamespace(execute=AsyncMock())
    adapter._maturations = SimpleNamespace(execute=AsyncMock())
    adapter._balance = SimpleNamespace(execute=AsyncMock())
    adapter._roles = SimpleNamespace(execute=AsyncMock())
    adapter._documents = SimpleNamespace(execute=AsyncMock())
    upload_validation = AsyncMock()
    monkeypatch.setattr(adapter, "_validate_document_upload_path", upload_validation)

    cases = (
        (FunctionId.EMP_CREATE_001, adapter._employees.create_employee),
        (FunctionId.EMP_UPDATE_001, adapter._summary.execute),
        (FunctionId.EMP_CONNECT_001, adapter._summary.execute),
        (FunctionId.EMP_CONTRACT_002, adapter._contracts.execute),
        (FunctionId.EMP_CONTRACT_003, adapter._contracts.execute),
        (FunctionId.EMP_MAT_002, adapter._maturations.execute),
        (FunctionId.EMP_BAL_002, adapter._balance.execute),
        (FunctionId.EMP_RBAC_002, adapter._roles.execute),
        (FunctionId.EMP_DOC_002, adapter._documents.execute),
        (FunctionId.EMP_DOC_004, adapter._documents.execute),
        (FunctionId.EMP_DOC_005, adapter._documents.execute),
    )
    for function_id, method in cases:
        action = _action(function_id)
        before = method.await_count
        await adapter._dispatch_write(action)
        assert method.await_count == before + 1
    upload_validation.assert_awaited_once()

    with pytest.raises(DicWriteDisabledError, match="download"):
        await adapter._dispatch_write(_action(FunctionId.EMP_DOC_003))
    with pytest.raises(DicWriteDisabledError, match="exports"):
        await adapter._dispatch_write(_action(FunctionId.EMP_EXPORT_001, employee_id=None))
    with pytest.raises(DicValidationError, match="no deterministic write plan"):
        await adapter._dispatch_write(_action(FunctionId.EMP_READ_001))


@pytest.mark.asyncio
async def test_document_upload_path_must_be_inside_configured_quarantine(tmp_path) -> None:
    quarantine = (tmp_path / "quarantine").resolve()
    quarantine.mkdir()
    inside = quarantine / "synthetic.pdf"
    inside.write_bytes(b"synthetic")
    adapter, _ = _adapter(quarantine_root=quarantine)
    await adapter._validate_document_upload_path(
        _action(FunctionId.EMP_DOC_002, {"safe_local_path": str(inside)})
    )

    no_root, _ = _adapter()
    with pytest.raises(DicWriteDisabledError, match="quarantine root"):
        await no_root._validate_document_upload_path(_action(FunctionId.EMP_DOC_002))
    with pytest.raises(DicValidationError, match="quarantined local path"):
        await adapter._validate_document_upload_path(
            _action(FunctionId.EMP_DOC_002, {"safe_local_path": 1})
        )
    with pytest.raises(DicValidationError, match="unavailable"):
        await adapter._validate_document_upload_path(
            _action(FunctionId.EMP_DOC_002, {"safe_local_path": str(quarantine / "missing")})
        )
    outside = (tmp_path / "outside.pdf").resolve()
    outside.write_bytes(b"synthetic")
    with pytest.raises(DicValidationError, match="outside quarantine"):
        await adapter._validate_document_upload_path(
            _action(FunctionId.EMP_DOC_002, {"safe_local_path": str(outside)})
        )


@pytest.mark.asyncio
async def test_execute_prepared_handles_verified_and_ambiguous_outcomes(monkeypatch) -> None:
    with pytest.raises(DicWriteDisabledError, match="explicitly configured"):
        PlaywrightDicAdapter(  # type: ignore[arg-type]
            UnusedSyntheticPage(), live_writes_enabled=True
        )

    adapter, coordinator = _adapter(live_writes_enabled=True)
    adapter._auth = _auth()
    dispatch = AsyncMock()
    monkeypatch.setattr(adapter, "_dispatch_write", dispatch)
    action = _action(FunctionId.EMP_UPDATE_001, {"job_title": "Synthetic"})
    reconcile = AsyncMock(
        return_value=ReconciliationResult(
            action_id=action.action_id,
            state=ReconciliationState.CONFIRMED_APPLIED,
            detail="synthetic applied",
        )
    )
    monkeypatch.setattr(adapter, "reconcile", reconcile)
    result = await adapter.execute_prepared(action)
    assert result.postcondition_verified is True
    assert result.message == "write applied and postcondition verified"

    coordinator.write_error = TimeoutError("synthetic uncertain response")
    recovered = await adapter.execute_prepared(action)
    assert "recovered" in recovered.message

    reconcile.return_value = ReconciliationResult(
        action_id=action.action_id,
        state=ReconciliationState.CONFIRMED_NOT_APPLIED,
        detail="synthetic not applied",
    )
    with pytest.raises(DicAmbiguousWriteOutcomeError):
        await adapter.execute_prepared(action)
    reconcile.return_value = ReconciliationResult(
        action_id=action.action_id,
        state=ReconciliationState.UNKNOWN,
        detail="synthetic unknown",
    )
    with pytest.raises(DicReconciliationRequiredError, match="synthetic unknown"):
        await adapter.execute_prepared(action)

    coordinator.write_error = None
    with pytest.raises(DicReconciliationRequiredError, match="synthetic unknown"):
        await adapter.execute_prepared(action)

    adapter._auth = _auth(SessionState.UNKNOWN)
    with pytest.raises(DicAuthenticationError, match="tenant-bound"):
        await adapter.execute_prepared(action)


@pytest.mark.asyncio
async def test_reconciliation_covers_safe_postconditions_and_unknown_cases() -> None:
    adapter, _ = _adapter()
    employee_id = "EMP-SYNTH-001"
    applied_item = EmployeeListItem(
        employee_id=employee_id,
        display_name_redacted="A. E.",
        account_state=AccountState.CONNECTED,
    )
    adapter._employees = SimpleNamespace(
        list=AsyncMock(
            return_value=EmployeeListResult(
                items=(applied_item,), page=1, page_size=100, total=1, has_next=False
            )
        )
    )
    adapter._summary = SimpleNamespace(
        read=AsyncMock(
            return_value=EmployeeSummary(
                employee_id=employee_id,
                job_title="Synthetic Lead",
                state=EmployeeState.INACTIVE,
            )
        )
    )
    adapter._contracts = SimpleNamespace(
        read=AsyncMock(
            return_value=(ContractRecord(contract_id="CON-SYNTH-001", employee_id=employee_id),)
        )
    )
    adapter._maturations = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                MaturationRecord(
                    maturation_id="MAT-SYNTH-001", employee_id=employee_id, category="Ferie"
                ),
            )
        )
    )
    adapter._roles = SimpleNamespace(
        read_time_access=AsyncMock(
            return_value=TimeAccessResult(employee_id=employee_id, timestamping_enabled=True)
        )
    )
    adapter._documents = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                DocumentMetadata(
                    document_id="DOC-SYNTH-001",
                    employee_id=employee_id,
                    title_redacted="[REDACTED]",
                ),
            )
        )
    )

    create_unknown = await adapter._reconcile_direct(
        _action(FunctionId.EMP_CREATE_001, {}, employee_id=None)
    )
    assert create_unknown.state is ReconciliationState.UNKNOWN
    create_applied = await adapter._reconcile_direct(
        _action(
            FunctionId.EMP_CREATE_001,
            {"employee_id": employee_id},
            employee_id=None,
        )
    )
    assert create_applied.state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_EXPORT_001, employee_id=None))
    ).state is ReconciliationState.UNKNOWN

    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_STATUS_001))
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_UPDATE_001, {"job_title": " synthetic lead "})
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_UPDATE_001, {"first_name": "Alice"}))
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_CONNECT_001))
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_DELETE_001))
    ).state is ReconciliationState.UNKNOWN

    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_CONTRACT_002, {"contract_id": "CON-SYNTH-001"})
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_CONTRACT_002))
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_MAT_002, {"category": "ferie"}))
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_RBAC_002, {"timestamping_enabled": True})
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_RBAC_002, {"roles": []}))
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DOC_004, {"document_id": "DOC-SYNTH-001"})
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DOC_005, {"document_id": "DOC-SYNTH-001"})
        )
    ).state is ReconciliationState.CONFIRMED_NOT_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_BAL_002))
    ).state is ReconciliationState.UNKNOWN

    public = await adapter.reconcile(_action(FunctionId.EMP_STATUS_001))
    assert public.state is ReconciliationState.CONFIRMED_APPLIED
    assert adapter._compare_expected(" Synthetic ", "synthetic") is True
    assert adapter._compare_expected(1, 1) is True
