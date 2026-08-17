from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import JsonValue, SecretStr

from bh_dic.dic.auth import DicAuthOutcomeUnknownError, DicAuthStage
from bh_dic.dic.catalog import MUTATING_FUNCTIONS
from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicAuthenticationError,
    DicAuthorizationError,
    DicCircuitOpenError,
    DicConfigurationError,
    DicReconciliationRequiredError,
    DicUiChangedError,
    DicValidationError,
    DicWriteDisabledError,
)
from bh_dic.dic.models import (
    AccountState,
    BalanceCorrectionState,
    BalanceResult,
    ContractRecord,
    DicCredentials,
    DocumentMetadata,
    DocumentQuery,
    EmployeeFilter,
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
    RoleAssignment,
    RolesResult,
    SessionState,
    SessionStatus,
    TimeAccessResult,
)
from bh_dic.dic.pages import VerifiedUploadPayload
from bh_dic.dic.playwright_adapter import PlaywrightDicAdapter, _WriteBaseline
from bh_dic.logging import JsonFormatter
from bh_dic.services.browser_runtime import BrowserCoordinator, CircuitBreaker, ReadRetryPolicy


class UnusedSyntheticPage:
    """Page Objects are replaced before any synthetic adapter operation reaches the DOM."""


class DirectCoordinator:
    def __init__(self) -> None:
        self.write_error: Exception | None = None
        self.closed = False
        self.once_calls: list[tuple[str, float | None]] = []

    async def run_read(self, name, lock_key, operation):
        del name, lock_key
        return await operation()

    async def run_once(self, name, lock_key, operation, *, timeout_seconds=None):
        del lock_key
        self.once_calls.append((name, timeout_seconds))
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
    verified_session_callback: Callable[[], Awaitable[None]] | None = None,
) -> tuple[PlaywrightDicAdapter, DirectCoordinator]:
    direct = coordinator or DirectCoordinator()
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=direct,  # type: ignore[arg-type]
        expected_tenant_id="123456789",
        quarantine_root=quarantine_root,
        live_writes_enabled=live_writes_enabled,
        state_digest_key=b"s" * 32,
        verified_session_callback=verified_session_callback,
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
    assert unavailable.ready is False
    assert unavailable.authenticated is False
    assert unavailable.browser_available is True

    adapter._auth = _auth(SessionState.MISSING)
    missing = await adapter.health()
    assert missing.ready is False
    assert missing.authenticated is False
    assert missing.browser_available is True
    assert missing.detail == "browser ready; authenticated tenant is unavailable"

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


@pytest.mark.parametrize(
    "failure",
    [
        DicUiChangedError("private-ui-drift-marker"),
        DicCircuitOpenError("private-circuit-marker"),
        TimeoutError("private-timeout-marker"),
    ],
    ids=("ui-drift", "circuit-open", "timeout"),
)
@pytest.mark.asyncio
async def test_health_reports_safe_degraded_status_for_expected_dic_failures(
    failure: Exception,
) -> None:
    adapter, _ = _adapter()
    adapter._auth = _auth()
    adapter._auth.status = AsyncMock(side_effect=failure)

    health = await adapter.health()

    assert health.ready is False
    assert health.authenticated is False
    assert health.browser_available is True
    assert health.detail == "browser ready; authenticated tenant is unavailable"
    assert "private" not in repr(health).casefold()


@pytest.mark.asyncio
async def test_auth_operations_use_single_run_with_login_budget_override() -> None:
    coordinator = DirectCoordinator()
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=coordinator,  # type: ignore[arg-type]
        expected_tenant_id="123456789",
        login_timeout_ms=60_000,
    )
    adapter._auth = _auth(SessionState.UNKNOWN)
    credentials = DicCredentials(
        username="synthetic",
        password=SecretStr("synthetic-password"),
    )

    result = await adapter.ensure_authenticated(credentials)

    assert result.state is SessionState.AUTHENTICATED
    assert coordinator.once_calls == [
        ("session_status", 65.0),
        ("authenticate", 65.0),
    ]


@pytest.mark.asyncio
async def test_auth_transport_failure_after_dispatch_is_never_retried() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(
            attempts=3,
            initial_delay_seconds=0,
            maximum_delay_seconds=0,
            operation_timeout_seconds=1,
        ),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=coordinator,
        expected_tenant_id="123456789",
        login_timeout_ms=5_000,
    )
    auth = _auth(SessionState.UNKNOWN)
    auth.authenticate = AsyncMock(
        side_effect=DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
    )
    adapter._auth = auth

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await adapter.ensure_authenticated(
            DicCredentials(
                username="synthetic",
                password=SecretStr("synthetic-password"),
            )
        )

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    auth.authenticate.assert_awaited_once()
    await adapter.close()


@pytest.mark.asyncio
async def test_external_queue_wait_cancellation_after_auth_dispatch_is_unknown() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(operation_timeout_seconds=5),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=coordinator,
        expected_tenant_id="123456789",
        login_timeout_ms=5_000,
    )
    click_started = asyncio.Event()
    release_worker = asyncio.Event()
    status_calls = 0
    authenticate_calls = 0
    clicks = 0

    class QueueOwnedAuth:
        async def status(self) -> SessionStatus:
            nonlocal status_calls
            status_calls += 1
            return SessionStatus(state=SessionState.UNKNOWN)

        async def authenticate(self, _credentials: DicCredentials) -> SessionStatus:
            nonlocal authenticate_calls, clicks
            authenticate_calls += 1
            clicks += 1
            click_started.set()
            await release_worker.wait()
            return SessionStatus(state=SessionState.AUTHENTICATED)

    adapter._auth = QueueOwnedAuth()  # type: ignore[assignment]
    operation = asyncio.create_task(
        asyncio.wait_for(
            adapter.ensure_authenticated(
                DicCredentials(
                    username="synthetic",
                    password=SecretStr("synthetic-password"),
                )
            ),
            timeout=0.05,
        )
    )
    await asyncio.wait_for(click_started.wait(), timeout=1)

    try:
        with pytest.raises(DicAuthOutcomeUnknownError) as caught:
            await operation
        assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert status_calls == 1
        assert authenticate_calls == 1
        assert clicks == 1
    finally:
        release_worker.set()
        await adapter.close()


@pytest.mark.asyncio
async def test_external_session_status_cancellation_remains_precredential() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(operation_timeout_seconds=5),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedSyntheticPage(),
        coordinator=coordinator,
        expected_tenant_id="123456789",
        login_timeout_ms=5_000,
    )
    status_started = asyncio.Event()
    release_worker = asyncio.Event()
    authenticate_calls = 0

    class QueueOwnedAuth:
        async def status(self) -> SessionStatus:
            status_started.set()
            await release_worker.wait()
            return SessionStatus(state=SessionState.UNKNOWN)

        async def authenticate(self, _credentials: DicCredentials) -> SessionStatus:
            nonlocal authenticate_calls
            authenticate_calls += 1
            return SessionStatus(state=SessionState.AUTHENTICATED)

    adapter._auth = QueueOwnedAuth()  # type: ignore[assignment]
    operation = asyncio.create_task(asyncio.wait_for(adapter.session_status(), timeout=0.05))
    await asyncio.wait_for(status_started.wait(), timeout=1)

    try:
        with pytest.raises(TimeoutError):
            await operation
        assert authenticate_calls == 0
    finally:
        release_worker.set()
        await adapter.close()


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
async def test_verified_session_callback_runs_only_after_attested_success() -> None:
    events: list[str] = []

    async def persist_verified_session() -> None:
        events.append("persist")

    adapter, _ = _adapter(verified_session_callback=persist_verified_session)
    adapter._auth = _auth()

    async def successful_list(_query: EmployeeListQuery) -> EmployeeListResult:
        events.append("read")
        return EmployeeListResult(items=(), page=1, page_size=25, total=0, has_next=False)

    adapter._employees = SimpleNamespace(list=successful_list)

    assert (await adapter.session_status()).state is SessionState.AUTHENTICATED
    assert events == ["persist"]

    events.clear()
    await adapter.list_employees(EmployeeListQuery())
    assert events == ["read", "persist"]

    # Explicit authentication is persisted by the composition root, not by the
    # passive callback, so this probe must not write the vault twice.
    events.clear()
    assert (await adapter.ensure_authenticated()).state is SessionState.AUTHENTICATED
    assert events == []

    adapter._auth = _auth(SessionState.UNKNOWN)
    assert (await adapter.session_status()).state is SessionState.UNKNOWN
    assert events == []
    with pytest.raises(DicAuthenticationError, match="tenant-bound"):
        await adapter.list_employees(EmployeeListQuery())
    assert events == []


@pytest.mark.asyncio
async def test_failed_read_never_notifies_verified_session_callback() -> None:
    callback = AsyncMock()
    adapter, _ = _adapter(verified_session_callback=callback)
    adapter._auth = _auth()
    adapter._employees = SimpleNamespace(
        list=AsyncMock(side_effect=DicUiChangedError("private-read-failure"))
    )

    with pytest.raises(DicUiChangedError, match="private-read-failure"):
        await adapter.list_employees(EmployeeListQuery())

    callback.assert_not_awaited()


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
    sanitized_upload = _action(
        FunctionId.EMP_DOC_002,
        {"category": "CV"},
    )
    upload_payload = VerifiedUploadPayload(
        name="document-upload.pdf",
        mime_type="application/pdf",
        buffer=b"synthetic",
    )
    upload_validation = AsyncMock(return_value=(sanitized_upload, upload_payload))
    monkeypatch.setattr(adapter, "_validated_document_upload_action", upload_validation)

    cases = (
        (FunctionId.EMP_CREATE_001, adapter._employees.create_employee),
        (FunctionId.EMP_UPDATE_001, adapter._summary.execute),
        (FunctionId.EMP_CONNECT_001, adapter._summary.execute),
        (FunctionId.EMP_CONTRACT_002, adapter._contracts.execute),
        (FunctionId.EMP_MAT_002, adapter._maturations.execute),
        (FunctionId.EMP_BAL_002, adapter._balance.execute),
        (FunctionId.EMP_RBAC_002, adapter._roles.execute),
        (FunctionId.EMP_DOC_002, adapter._documents.execute),
        (FunctionId.EMP_DOC_004, adapter._documents.execute),
    )
    for function_id, method in cases:
        action = _action(function_id)
        before = method.await_count
        await adapter._dispatch_write(action)
        assert method.await_count == before + 1
    upload_validation.assert_awaited_once()
    adapter._documents.execute.assert_any_await(
        sanitized_upload,
        verified_upload=upload_payload,
    )

    for unavailable in (
        FunctionId.EMP_INVITE_001,
        FunctionId.EMP_CONTRACT_003,
        FunctionId.EMP_DOC_003,
        FunctionId.EMP_DOC_005,
        FunctionId.EMP_EXPORT_001,
    ):
        with pytest.raises(DicWriteDisabledError, match="no exhaustive observable"):
            await adapter._dispatch_write(
                _action(
                    unavailable,
                    employee_id=(
                        None if unavailable is FunctionId.EMP_EXPORT_001 else "EMP-SYNTH-001"
                    ),
                )
            )
    with pytest.raises(DicValidationError, match="no deterministic write plan"):
        await adapter._dispatch_write(_action(FunctionId.EMP_READ_001))


@pytest.mark.asyncio
async def test_document_upload_path_must_be_inside_configured_quarantine(
    tmp_path, monkeypatch
) -> None:
    quarantine = (tmp_path / "quarantine").resolve()
    quarantine.mkdir()
    inside = quarantine / "7e57d004-2b97-4b22-9e41-4d548c3c1122"
    inside.write_bytes(b"synthetic")
    adapter, _ = _adapter(quarantine_root=quarantine)
    digest = hashlib.sha256(inside.read_bytes()).hexdigest()
    execution_parameters: dict[str, JsonValue] = {
        "safe_local_path": str(inside),
        "safe_local_sha256": digest,
        "safe_local_size": inside.stat().st_size,
        "detected_mime": "application/pdf",
        "category": "CV",
    }
    sanitized, verified_upload = await adapter._validated_document_upload_action(
        _action(FunctionId.EMP_DOC_002, execution_parameters)
    )
    assert verified_upload.name == "document-upload.pdf"
    assert verified_upload.mime_type == "application/pdf"
    assert verified_upload.buffer == b"synthetic"
    assert str(inside) not in repr(verified_upload)
    assert b"synthetic".__repr__() not in repr(verified_upload)
    assert sanitized.parameters == {"category": "CV"}
    assert {
        "safe_local_path",
        "safe_local_sha256",
        "safe_local_size",
        "detected_mime",
    }.isdisjoint(sanitized.parameters)
    assert "safe_local_path" not in repr(sanitized)
    assert str(inside) not in repr(sanitized)

    no_root, _ = _adapter()
    with pytest.raises(DicWriteDisabledError, match="quarantine root"):
        await no_root._validated_document_upload_action(_action(FunctionId.EMP_DOC_002))
    with pytest.raises(DicValidationError, match="quarantined local path"):
        await adapter._validated_document_upload_action(
            _action(FunctionId.EMP_DOC_002, {"safe_local_path": 1})
        )
    missing = quarantine / "opaque-missing-upload"
    with pytest.raises(DicValidationError, match="unavailable") as missing_error:
        await adapter._validated_document_upload_action(
            _action(
                FunctionId.EMP_DOC_002,
                {**execution_parameters, "safe_local_path": str(missing)},
            )
        )
    record = logging.LogRecord(
        "bh_dic.browser",
        logging.ERROR,
        __file__,
        1,
        "upload failed",
        (),
        (DicValidationError, missing_error.value, missing_error.value.__traceback__),
    )
    rendered_error = JsonFormatter(timezone="UTC").format(record)
    assert str(missing) not in rendered_error
    assert "opaque-missing-upload" not in rendered_error

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError(f"cannot open {inside}")

    monkeypatch.setattr(adapter, "_load_verified_upload", fail_open)
    with pytest.raises(DicValidationError, match="unavailable") as open_error:
        await adapter._validated_document_upload_action(
            _action(FunctionId.EMP_DOC_002, execution_parameters)
        )
    open_record = logging.LogRecord(
        "bh_dic.browser",
        logging.ERROR,
        __file__,
        1,
        "upload failed",
        (),
        (DicValidationError, open_error.value, open_error.value.__traceback__),
    )
    rendered_open_error = JsonFormatter(timezone="UTC").format(open_record)
    assert str(inside) not in rendered_open_error
    assert inside.name not in rendered_open_error
    monkeypatch.undo()
    outside = (tmp_path / "outside.pdf").resolve()
    outside.write_bytes(b"synthetic")
    with pytest.raises(DicValidationError, match="outside quarantine"):
        await adapter._validated_document_upload_action(
            _action(
                FunctionId.EMP_DOC_002, {**execution_parameters, "safe_local_path": str(outside)}
            )
        )
    with pytest.raises(DicValidationError, match="integrity changed"):
        await adapter._validated_document_upload_action(
            _action(
                FunctionId.EMP_DOC_002,
                {**execution_parameters, "safe_local_sha256": "0" * 64},
            )
        )
    with pytest.raises(DicValidationError, match="integrity changed"):
        await adapter._validated_document_upload_action(
            _action(
                FunctionId.EMP_DOC_002,
                {**execution_parameters, "safe_local_size": inside.stat().st_size + 1},
            )
        )


@pytest.mark.asyncio
async def test_upload_capability_is_sanitized_before_pom_and_never_returned(
    tmp_path, caplog
) -> None:
    quarantine = (tmp_path / "quarantine").resolve()
    quarantine.mkdir()
    claimed = quarantine / "claimed.pdf"
    claimed.write_bytes(b"synthetic claimed document")
    sha256 = hashlib.sha256(claimed.read_bytes()).hexdigest()
    action = _action(
        FunctionId.EMP_DOC_002,
        {
            "safe_local_path": str(claimed),
            "safe_local_sha256": sha256,
            "safe_local_size": claimed.stat().st_size,
            "detected_mime": "application/pdf",
            "category": "CV",
        },
    )
    adapter, _ = _adapter(
        quarantine_root=quarantine,
        live_writes_enabled=True,
    )
    adapter._auth = _auth()
    adapter._documents = SimpleNamespace(
        stable_document_ids=AsyncMock(return_value=frozenset({"DOC-OLD-001"})),
        execute=AsyncMock(),
        verify_uploaded_document=AsyncMock(return_value=True),
    )

    result = await adapter.execute_prepared(action)

    pom_action = adapter._documents.execute.await_args.args[0]
    assert pom_action.parameters == {
        "category": "CV",
    }
    verified_upload = adapter._documents.execute.await_args.kwargs["verified_upload"]
    assert isinstance(verified_upload, VerifiedUploadPayload)
    assert verified_upload.name == "document-upload.pdf"
    assert verified_upload.mime_type == "application/pdf"
    assert verified_upload.buffer == claimed.read_bytes()
    assert action.request_fingerprint == "b" * 64
    digest_scope = adapter._state_scope(action.function_id, action.employee_id, action.parameters)
    rendered = " ".join((repr(pom_action), result.model_dump_json(), caplog.text))
    for internal_name in (
        "safe_local_path",
        "safe_local_sha256",
        "safe_local_size",
        "detected_mime",
    ):
        assert internal_name not in rendered
        assert internal_name not in digest_scope
    assert str(claimed) not in rendered
    assert str(claimed) not in digest_scope
    assert sha256 not in rendered
    assert sha256 not in digest_scope
    assert result.details == {}


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
    monkeypatch.setattr(adapter, "_capture_write_baseline", AsyncMock(return_value=None))
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
@pytest.mark.parametrize(
    "write_error",
    [
        None,
        TimeoutError("synthetic ambiguous dispatch"),
        PlaywrightTimeoutError("synthetic Playwright timeout after submit"),
    ],
)
async def test_reconciliation_exception_after_possible_dispatch_is_always_unknown(
    monkeypatch, write_error
) -> None:
    adapter, coordinator = _adapter(live_writes_enabled=True)
    adapter._auth = _auth()
    monkeypatch.setattr(adapter, "_dispatch_write", AsyncMock())
    monkeypatch.setattr(adapter, "_capture_write_baseline", AsyncMock(return_value=None))
    action = _action(FunctionId.EMP_UPDATE_001, {"job_title": "Synthetic"})
    reconcile = AsyncMock(side_effect=RuntimeError("synthetic reconcile failure"))
    monkeypatch.setattr(adapter, "reconcile", reconcile)
    coordinator.write_error = write_error

    with pytest.raises(DicReconciliationRequiredError, match="outcome is unknown") as caught:
        await adapter.execute_prepared(action)

    assert isinstance(caught.value.__cause__, RuntimeError)
    reconcile.assert_awaited_once_with(action)


@pytest.mark.asyncio
async def test_state_digest_routes_every_write_to_raw_pom_state() -> None:
    adapter, _ = _adapter()
    adapter._auth = _auth()
    employee_id = "EMP-SYNTH-001"
    digest = "d" * 64

    def pom(**methods):
        return SimpleNamespace(
            opaque_state_digest=AsyncMock(return_value=digest),
            **{name: AsyncMock(return_value=value) for name, value in methods.items()},
        )

    list_result = EmployeeListResult(items=(), page=1, page_size=100, total=0, has_next=False)
    adapter._employees = pom(
        list=list_result,
        stable_employee_ids_for_create=frozenset(),
    )
    adapter._summary = pom(open=None, verify_expected=False)
    adapter._contracts = pom(
        read=(
            ContractRecord(
                contract_id="CON-SYNTH-001",
                employee_id=employee_id,
                stable_identifier=True,
                actionable=True,
            ),
        ),
        verify_expected=False,
    )
    adapter._maturations = pom(read=())
    adapter._balance = pom(
        read_correction_state=BalanceCorrectionState(
            employee_id=employee_id,
            year=2026,
            month=8,
            category="Ferie",
            current_value="0",
        )
    )
    adapter._roles = pom(
        read_roles=RolesResult(
            employee_id=employee_id,
            roles=(RoleAssignment(name="Employee", enabled=True),),
        ),
        read_time_access=TimeAccessResult(employee_id=employee_id),
    )
    adapter._documents = pom(
        read=(
            DocumentMetadata(
                document_id="DOC-SYNTH-001",
                employee_id=employee_id,
                stable_identifier=True,
                actionable=True,
                title_redacted="[REDACTED]",
            ),
        ),
        verify_expected_metadata=False,
    )
    cases: dict[FunctionId, tuple[str | None, dict[str, JsonValue]]] = {
        FunctionId.EMP_UPDATE_001: (employee_id, {"job_title": "Changed"}),
        FunctionId.EMP_CREATE_001: (
            None,
            {"creation_mode": "manual", "first_name": "Alice", "last_name": "Example"},
        ),
        FunctionId.EMP_CONTRACT_002: (employee_id, {}),
        FunctionId.EMP_CONTRACT_003: (
            employee_id,
            {"contract_id": "CON-SYNTH-001"},
        ),
        FunctionId.EMP_MAT_002: (
            employee_id,
            {"category": "Ferie", "valid_from": "2026-01-01"},
        ),
        FunctionId.EMP_BAL_002: (
            employee_id,
            {
                "year": 2026,
                "month": 8,
                "category": "Ferie",
                "previous_value": "0",
                "amount": "1",
            },
        ),
        FunctionId.EMP_CONNECT_001: (employee_id, {}),
        FunctionId.EMP_CONNECT_002: (employee_id, {}),
        FunctionId.EMP_INVITE_001: (employee_id, {}),
        FunctionId.EMP_INVITE_002: (employee_id, {}),
        FunctionId.EMP_RBAC_002: (
            employee_id,
            {"role_name": "Employee", "enabled": False},
        ),
        FunctionId.EMP_STATUS_001: (employee_id, {}),
        FunctionId.EMP_STATUS_002: (employee_id, {}),
        FunctionId.EMP_DOC_002: (employee_id, {"title": "Synthetic", "category": "CV"}),
        FunctionId.EMP_DOC_003: (employee_id, {"document_id": "DOC-SYNTH-001"}),
        FunctionId.EMP_DOC_004: (
            employee_id,
            {"document_id": "DOC-SYNTH-001", "category": "Changed"},
        ),
        FunctionId.EMP_DOC_005: (employee_id, {"document_id": "DOC-SYNTH-001"}),
        FunctionId.EMP_DELETE_001: (employee_id, {}),
        FunctionId.EMP_EXPORT_001: (None, {}),
    }
    assert frozenset(cases) == MUTATING_FUNCTIONS
    for function_id, (target, parameters) in cases.items():
        assert await adapter.get_state_digest(function_id, target, parameters) == digest

    with pytest.raises(DicWriteDisabledError, match="cannot verify every requested field"):
        await adapter.get_state_digest(
            FunctionId.EMP_CREATE_001,
            None,
            {"first_name": "Alice", "last_name": "Example", "iban": "SYNTHETIC"},
        )

    all_summary_fields: dict[str, JsonValue] = {
        "first_name": "Alice",
        "last_name": "Example",
        "payroll_number": "SYN-001",
        "tax_code": "SYNTHETIC000X",
        "birth_date": "2000-01-01",
        "iban": "IT00SYNTHETIC0000000000000",
        "job_title": "Synthetic tester",
        "phone": "+390000000000",
        "business_email": "alice@example.invalid",
        "address": "Synthetic address",
        "workplace": "Synthetic office",
        "notes": "Synthetic notes",
    }
    adapter._summary.verify_expected.return_value = True
    summary_digest_calls = adapter._summary.opaque_state_digest.await_count
    with pytest.raises(DicValidationError, match="would not change"):
        await adapter.get_state_digest(
            FunctionId.EMP_UPDATE_001,
            employee_id,
            all_summary_fields,
        )
    adapter._summary.verify_expected.assert_awaited_with(employee_id, all_summary_fields)
    assert adapter._summary.opaque_state_digest.await_count == summary_digest_calls
    adapter._summary.verify_expected.return_value = False

    adapter._contracts.verify_expected.return_value = True
    contract_digest_calls = adapter._contracts.opaque_state_digest.await_count
    with pytest.raises(DicValidationError, match="would not change"):
        await adapter.get_state_digest(
            FunctionId.EMP_CONTRACT_002,
            employee_id,
            {"contract_id": "CON-SYNTH-001", "description": "Same raw description"},
        )
    assert adapter._contracts.opaque_state_digest.await_count == contract_digest_calls
    adapter._contracts.verify_expected.return_value = False

    adapter._state_digest_key = None
    with pytest.raises(DicConfigurationError, match="not configured"):
        await adapter.get_state_digest(FunctionId.EMP_CREATE_001, None, {})


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
        ),
        verify_created_employee=AsyncMock(return_value=True),
    )
    adapter._summary = SimpleNamespace(
        read=AsyncMock(
            return_value=EmployeeSummary(
                employee_id=employee_id,
                job_title="Synthetic Lead",
                state=EmployeeState.INACTIVE,
            )
        ),
        verify_expected=AsyncMock(return_value=True),
        open=AsyncMock(),
        opaque_state_digest=AsyncMock(return_value="b" * 64),
    )
    adapter._contracts = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                ContractRecord(
                    contract_id="CON-SYNTH-001",
                    employee_id=employee_id,
                    stable_identifier=True,
                    actionable=True,
                ),
            )
        ),
        verify_expected=AsyncMock(return_value=True),
        verify_created_contract=AsyncMock(return_value=True),
        opaque_state_digest=AsyncMock(return_value="b" * 64),
    )
    adapter._maturations = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                MaturationRecord(
                    maturation_id="MAT-SYNTH-001",
                    employee_id=employee_id,
                    category="Ferie",
                    valid_from="2026-01-01",
                ),
            )
        ),
        verify_created_maturation=AsyncMock(return_value=True),
    )
    adapter._roles = SimpleNamespace(
        read_roles=AsyncMock(
            return_value=RolesResult(
                employee_id=employee_id,
                roles=(RoleAssignment(name="Employee", enabled=False),),
            )
        ),
        read_time_access=AsyncMock(
            return_value=TimeAccessResult(employee_id=employee_id, timestamping_enabled=True)
        ),
    )
    adapter._documents = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                DocumentMetadata(
                    document_id="DOC-SYNTH-001",
                    employee_id=employee_id,
                    stable_identifier=True,
                    actionable=True,
                    title_redacted="[REDACTED]",
                ),
            )
        ),
        verify_expected_metadata=AsyncMock(return_value=True),
        verify_uploaded_document=AsyncMock(return_value=True),
        opaque_state_digest=AsyncMock(return_value="b" * 64),
    )
    adapter._balance = SimpleNamespace(
        read_correction_state=AsyncMock(
            return_value=BalanceCorrectionState(
                employee_id=employee_id,
                year=2026,
                month=8,
                category="Ferie",
                current_value="1",
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
            {"creation_mode": "manual", "first_name": "Alice", "last_name": "Example"},
            employee_id=None,
        ),
        baseline=_WriteBaseline(stable_ids=frozenset()),
    )
    assert create_applied.state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_EXPORT_001, employee_id=None))
    ).state is ReconciliationState.UNKNOWN

    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_STATUS_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_UPDATE_001, {"job_title": " synthetic lead "}),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_UPDATE_001, {"first_name": "Alice"}),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    adapter._summary.opaque_state_digest.return_value = "a" * 64
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_UPDATE_001, {"first_name": "Alice"}),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.UNKNOWN
    adapter._summary.opaque_state_digest.return_value = "b" * 64
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_CONNECT_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_INVITE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id}), digest="a" * 64),
        )
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DELETE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id})),
        )
    ).state is ReconciliationState.CONFIRMED_NOT_APPLIED
    adapter._employees.list.return_value = EmployeeListResult(
        items=(), page=1, page_size=100, total=0, has_next=False
    )
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DELETE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id})),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    adapter._employees.list.return_value = EmployeeListResult(
        items=(EmployeeListItem(employee_id="EMP-SYNTH-002", display_name_redacted="B. E."),),
        page=1,
        page_size=100,
        total=1,
        has_next=False,
    )
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DELETE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id})),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    adapter._employees.list.return_value = EmployeeListResult(
        items=(), page=1, page_size=100, total=101, has_next=True
    )
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DELETE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id})),
        )
    ).state is ReconciliationState.UNKNOWN
    adapter._employees.list.side_effect = RuntimeError("synthetic list failure")
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DELETE_001),
            baseline=_WriteBaseline(stable_ids=frozenset({employee_id})),
        )
    ).state is ReconciliationState.UNKNOWN
    adapter._employees.list.side_effect = None
    adapter._employees.list.return_value = EmployeeListResult(
        items=(applied_item,), page=1, page_size=100, total=1, has_next=False
    )
    delete_query = adapter._employees.list.await_args_list[-1].args[0]
    assert delete_query.query == employee_id
    assert delete_query.employee_filter is EmployeeFilter.ALL
    assert delete_query.page_size == 100

    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_CONTRACT_002,
                {"contract_id": "CON-SYNTH-001", "schedule": "40h"},
            ),
            baseline=_WriteBaseline(stable_ids=frozenset({"CON-SYNTH-001"}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    adapter._contracts.verify_expected.return_value = None
    adapter._contracts.verify_created_contract.return_value = None
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_CONTRACT_002, {"schedule": "40h"}),
            baseline=_WriteBaseline(stable_ids=frozenset({"CON-SYNTH-001"})),
        )
    ).state is ReconciliationState.UNKNOWN
    adapter._contracts.verify_expected.return_value = True
    adapter._contracts.verify_created_contract.return_value = True
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_CONTRACT_003, {"contract_id": "CON-SYNTH-001"}),
            baseline=_WriteBaseline(stable_ids=frozenset({"CON-SYNTH-001"})),
        )
    ).state is ReconciliationState.CONFIRMED_NOT_APPLIED
    adapter._contracts.read.return_value = (
        ContractRecord(
            contract_id="CON-SYNTH-OTHER",
            employee_id=employee_id,
            stable_identifier=True,
            actionable=True,
        ),
    )
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_CONTRACT_003, {"contract_id": "CON-SYNTH-001"}),
            baseline=_WriteBaseline(stable_ids=frozenset({"CON-SYNTH-001"})),
        )
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_MAT_002, {"category": "ferie"}))
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_MAT_002,
                {"category": "ferie", "valid_from": "2026-01-01"},
            ),
            baseline=_WriteBaseline(stable_ids=frozenset()),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_RBAC_002, {"timestamping_enabled": True})
        )
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_RBAC_002, {"role_name": "Employee", "enabled": False})
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_RBAC_002, {"roles": []}))
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_DOC_004,
                {"document_id": "DOC-SYNTH-001", "category": "CV"},
            ),
            baseline=_WriteBaseline(stable_ids=frozenset({"DOC-SYNTH-001"}), digest="a" * 64),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_DOC_002,
                {"category": "CV"},
            ),
            baseline=_WriteBaseline(stable_ids=frozenset({"DOC-SYNTH-001"})),
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_DOC_002,
                {"category": "CV"},
            )
        )
    ).state is ReconciliationState.UNKNOWN
    assert (
        await adapter._reconcile_direct(
            _action(FunctionId.EMP_DOC_005, {"document_id": "DOC-SYNTH-001"}),
            baseline=_WriteBaseline(stable_ids=frozenset({"DOC-SYNTH-001"})),
        )
    ).state is ReconciliationState.CONFIRMED_NOT_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_BAL_002,
                {"year": 2026, "month": 8, "category": "Ferie", "amount": "1"},
            )
        )
    ).state is ReconciliationState.CONFIRMED_APPLIED
    assert (
        await adapter._reconcile_direct(
            _action(
                FunctionId.EMP_BAL_002,
                {"year": 2026, "month": 8, "category": "Ferie", "amount": "2"},
            )
        )
    ).state is ReconciliationState.CONFIRMED_NOT_APPLIED
    assert (
        await adapter._reconcile_direct(_action(FunctionId.EMP_BAL_002))
    ).state is ReconciliationState.UNKNOWN

    public = await adapter.reconcile(_action(FunctionId.EMP_STATUS_001))
    assert public.state is ReconciliationState.UNKNOWN
    assert adapter._compare_expected(" Synthetic ", "synthetic") is True
    assert adapter._compare_expected(1, 1) is True
