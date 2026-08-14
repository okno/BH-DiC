"""Async Playwright implementation of the deterministic DIC adapter protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from bh_dic.dic.auth import PlaywrightAuthenticator
from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicAuthenticationError,
    DicAuthorizationError,
    DicConfigurationError,
    DicReconciliationRequiredError,
    DicUiChangedError,
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
    EmployeeFilter,
    EmployeeListQuery,
    EmployeeListResult,
    EmployeeState,
    EmployeeSummary,
    ExecutionResult,
    FunctionId,
    HealthStatus,
    MaturationRecord,
    OperationStatus,
    PayrollMetadata,
    PreparedAction,
    ReconciliationResult,
    ReconciliationState,
    RolesResult,
    SessionState,
    SessionStatus,
    TimeAccessResult,
)
from bh_dic.dic.pages import (
    EmployeeBalancePage,
    EmployeeContractsPage,
    EmployeeDocumentsPage,
    EmployeeMaturationsPage,
    EmployeePayrollsPage,
    EmployeeRolesPage,
    EmployeesListPage,
    EmployeeSummaryPage,
    PageLike,
    TimestampEmployeesPage,
)
from bh_dic.services.browser_runtime import BrowserCoordinator

T = TypeVar("T")


class PlaywrightDicAdapter:
    """No generic navigation/click API is exposed outside this class."""

    def __init__(
        self,
        page: PageLike,
        *,
        base_url: str = "https://secure.dipendentincloud.it",
        coordinator: BrowserCoordinator | None = None,
        expected_tenant_id: str | None = None,
        quarantine_root: Path | None = None,
        live_writes_enabled: bool = False,
    ) -> None:
        if live_writes_enabled and expected_tenant_id is None:
            raise DicWriteDisabledError("live writes require an explicitly configured DIC tenant")
        self._coordinator = coordinator or BrowserCoordinator()
        self._live_writes_enabled = live_writes_enabled
        self._quarantine_root = quarantine_root.resolve() if quarantine_root else None
        self._auth = PlaywrightAuthenticator(page, base_url, expected_tenant_id=expected_tenant_id)
        self._employees = EmployeesListPage(page, base_url)
        self._summary = EmployeeSummaryPage(page, base_url)
        self._roles = EmployeeRolesPage(page, base_url)
        self._timestamps = TimestampEmployeesPage(page, base_url)
        self._contracts = EmployeeContractsPage(page, base_url)
        self._maturations = EmployeeMaturationsPage(page, base_url)
        self._balance = EmployeeBalancePage(page, base_url)
        self._payrolls = EmployeePayrollsPage(page, base_url)
        self._documents = EmployeeDocumentsPage(page, base_url)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise DicValidationError("Playwright DIC adapter is closed")

    async def _read(self, name: str, operation: Callable[[], Awaitable[T]]) -> T:
        self._ensure_open()

        async def authenticated_operation() -> T:
            status = await self._auth.status()
            if status.state is not SessionState.AUTHENTICATED:
                raise DicAuthenticationError(
                    "an authenticated, tenant-bound DIC session is required"
                )
            return await operation()

        return await self._coordinator.run_read(name, "dic-browser", authenticated_operation)

    async def health(self) -> HealthStatus:
        if self._closed:
            return HealthStatus(
                ready=False,
                authenticated=False,
                browser_available=False,
                detail="adapter closed",
            )
        try:
            status = await self.session_status()
        except (DicAuthenticationError, DicAuthorizationError, DicConfigurationError):
            return HealthStatus(
                ready=True,
                authenticated=False,
                browser_available=True,
                detail="browser ready; authenticated tenant is unavailable",
            )
        return HealthStatus(
            ready=True,
            authenticated=status.state is SessionState.AUTHENTICATED,
            browser_available=True,
            detail="Playwright adapter ready",
        )

    async def ensure_authenticated(
        self, credentials: DicCredentials | None = None
    ) -> SessionStatus:
        self._ensure_open()
        current = await self.session_status()
        if current.state is SessionState.AUTHENTICATED:
            return current
        if credentials is None:
            raise DicAuthenticationError("DIC credentials are required for a new session")
        return await self._coordinator.run_read(
            "authenticate", "dic-browser", lambda: self._auth.authenticate(credentials)
        )

    async def session_status(self) -> SessionStatus:
        self._ensure_open()
        return await self._coordinator.run_read("session_status", "dic-browser", self._auth.status)

    async def list_employees(self, query: EmployeeListQuery) -> EmployeeListResult:
        return await self._read("employees.list", lambda: self._employees.list(query))

    async def get_employee_summary(self, employee_id: str) -> EmployeeSummary:
        return await self._read("employees.summary", lambda: self._summary.read(employee_id))

    async def get_contracts(self, employee_id: str) -> tuple[ContractRecord, ...]:
        return await self._read("employees.contracts", lambda: self._contracts.read(employee_id))

    async def get_roles(self, employee_id: str) -> RolesResult:
        return await self._read("employees.roles", lambda: self._roles.read_roles(employee_id))

    async def get_time_access(self, employee_id: str) -> TimeAccessResult:
        async def read_time_access() -> TimeAccessResult:
            result = await self._roles.read_time_access(employee_id)
            if result.timestamping_enabled is not None:
                return result
            timestamping_enabled = await self._timestamps.read_enabled(employee_id)
            return TimeAccessResult.model_validate(
                {**result.model_dump(), "timestamping_enabled": timestamping_enabled}
            )

        return await self._read("employees.time_access", read_time_access)

    async def get_maturations(self, employee_id: str) -> tuple[MaturationRecord, ...]:
        return await self._read(
            "employees.maturations", lambda: self._maturations.read(employee_id)
        )

    async def get_balance(self, employee_id: str, year: int) -> BalanceResult:
        return await self._read("employees.balance", lambda: self._balance.read(employee_id, year))

    async def get_payroll_metadata(
        self, employee_id: str, year: int | None = None
    ) -> tuple[PayrollMetadata, ...]:
        return await self._read(
            "employees.payrolls", lambda: self._payrolls.read(employee_id, year)
        )

    async def get_document_metadata(
        self, employee_id: str, query: DocumentQuery
    ) -> tuple[DocumentMetadata, ...]:
        return await self._read(
            "employees.documents", lambda: self._documents.read(employee_id, query)
        )

    async def _dispatch_write(self, action: PreparedAction) -> None:
        summary_functions = {
            FunctionId.EMP_UPDATE_001,
            FunctionId.EMP_CONNECT_001,
            FunctionId.EMP_CONNECT_002,
            FunctionId.EMP_INVITE_001,
            FunctionId.EMP_INVITE_002,
            FunctionId.EMP_STATUS_001,
            FunctionId.EMP_STATUS_002,
            FunctionId.EMP_DELETE_001,
        }
        if action.function_id is FunctionId.EMP_CREATE_001:
            await self._employees.create_employee(action)
        elif action.function_id in summary_functions:
            await self._summary.execute(action)
        elif action.function_id in {FunctionId.EMP_CONTRACT_002, FunctionId.EMP_CONTRACT_003}:
            await self._contracts.execute(action)
        elif action.function_id is FunctionId.EMP_MAT_002:
            await self._maturations.execute(action)
        elif action.function_id is FunctionId.EMP_BAL_002:
            await self._balance.execute(action)
        elif action.function_id is FunctionId.EMP_RBAC_002:
            await self._roles.execute(action)
        elif action.function_id in {
            FunctionId.EMP_DOC_002,
            FunctionId.EMP_DOC_004,
            FunctionId.EMP_DOC_005,
        }:
            if action.function_id is FunctionId.EMP_DOC_002:
                await self._validate_document_upload_path(action)
            await self._documents.execute(action)
        elif action.function_id is FunctionId.EMP_DOC_003:
            raise DicWriteDisabledError("document download is not exposed by the adapter")
        elif action.function_id is FunctionId.EMP_EXPORT_001:
            raise DicWriteDisabledError("exports require a protected artifact service")
        else:
            raise DicValidationError("function has no deterministic write plan")

    async def _validate_document_upload_path(self, action: PreparedAction) -> None:
        if self._quarantine_root is None:
            raise DicWriteDisabledError("document upload quarantine root is not configured")
        raw_path = action.parameters.get("safe_local_path")
        if not isinstance(raw_path, str):
            raise DicValidationError("document upload requires a quarantined local path")
        try:
            resolved = await asyncio.to_thread(Path(raw_path).resolve, True)
        except OSError as exc:
            raise DicValidationError("quarantined upload file is unavailable") from exc
        if not resolved.is_relative_to(self._quarantine_root):
            raise DicValidationError("document upload path is outside quarantine")

    async def execute_prepared(self, action: PreparedAction) -> ExecutionResult:
        self._ensure_open()
        if not self._live_writes_enabled:
            raise DicWriteDisabledError("Playwright live writes are disabled at adapter boundary")

        async def authenticated_write() -> None:
            status = await self._auth.status()
            if status.state is not SessionState.AUTHENTICATED:
                raise DicAuthenticationError(
                    "an authenticated, tenant-bound DIC session is required"
                )
            await self._dispatch_write(action)

        try:
            await self._coordinator.run_write(
                action.function_id.value,
                "dic-browser",
                authenticated_write,
            )
        except (TimeoutError, ConnectionError, DicUiChangedError) as exc:
            ambiguous = DicAmbiguousWriteOutcomeError(
                "write failed after dispatch; automatic retry is prohibited"
            )
            outcome = await self.reconcile(action)
            if outcome.state is ReconciliationState.CONFIRMED_APPLIED:
                return ExecutionResult(
                    action_id=action.action_id,
                    function_id=action.function_id,
                    status=OperationStatus.SUCCEEDED,
                    changed=True,
                    postcondition_verified=True,
                    message="write applied; response recovered by reconciliation",
                    correlation_id=action.correlation_id,
                )
            if outcome.state is ReconciliationState.CONFIRMED_NOT_APPLIED:
                raise ambiguous from exc
            raise DicReconciliationRequiredError(outcome.detail) from exc
        outcome = await self.reconcile(action)
        if outcome.state is not ReconciliationState.CONFIRMED_APPLIED:
            raise DicReconciliationRequiredError(outcome.detail)
        return ExecutionResult(
            action_id=action.action_id,
            function_id=action.function_id,
            status=OperationStatus.SUCCEEDED,
            changed=True,
            postcondition_verified=True,
            message="write applied and postcondition verified",
            correlation_id=action.correlation_id,
        )

    @staticmethod
    def _compare_expected(actual: object, expected: object) -> bool:
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.strip().casefold() == expected.strip().casefold()
        return actual == expected

    async def _reconcile_summary(self, action: PreparedAction) -> ReconciliationResult:
        if action.employee_id is None:
            raise DicValidationError("summary reconciliation requires employee_id")
        if action.function_id is FunctionId.EMP_STATUS_001:
            summary = await self._summary.read(action.employee_id)
            applied = summary.state is EmployeeState.INACTIVE
        elif action.function_id is FunctionId.EMP_STATUS_002:
            summary = await self._summary.read(action.employee_id)
            applied = summary.state is EmployeeState.ACTIVE
        elif action.function_id is FunctionId.EMP_UPDATE_001:
            summary = await self._summary.read(action.employee_id)
            comparable = {
                "payroll_number": summary.payroll_number,
                "job_title": summary.job_title,
                "workplace": summary.workplace,
            }
            expected = {key: value for key, value in action.parameters.items() if key in comparable}
            if not expected or set(action.parameters).difference(comparable):
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="sensitive summary fields cannot be safely compared after redaction",
                )
            applied = all(
                self._compare_expected(comparable[key], value) for key, value in expected.items()
            )
        else:
            query = EmployeeListQuery(
                query=action.employee_id, employee_filter=EmployeeFilter.ALL, page_size=100
            )
            result = await self._employees.list(query)
            matching = [item for item in result.items if item.employee_id == action.employee_id]
            if len(matching) != 1:
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="employee target is not uniquely visible during reconciliation",
                )
            account = matching[0].account_state
            expected_accounts = {
                FunctionId.EMP_CONNECT_001: AccountState.CONNECTED,
                FunctionId.EMP_CONNECT_002: AccountState.NOT_CONNECTED,
                FunctionId.EMP_INVITE_001: AccountState.INVITED,
                FunctionId.EMP_INVITE_002: AccountState.NOT_CONNECTED,
            }
            if action.function_id not in expected_accounts:
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="summary action has no safe automated postcondition",
                )
            applied = account is expected_accounts[action.function_id]
        return ReconciliationResult(
            action_id=action.action_id,
            state=(
                ReconciliationState.CONFIRMED_APPLIED
                if applied
                else ReconciliationState.CONFIRMED_NOT_APPLIED
            ),
            detail="summary postcondition compared against live read",
        )

    async def _reconcile_direct(self, action: PreparedAction) -> ReconciliationResult:
        if action.function_id is FunctionId.EMP_CREATE_001:
            employee_id = action.parameters.get("employee_id")
            if not isinstance(employee_id, str):
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="created employee identifier is not known",
                )
            result = await self._employees.list(
                EmployeeListQuery(
                    query=employee_id, employee_filter=EmployeeFilter.ALL, page_size=100
                )
            )
            applied = any(item.employee_id == employee_id for item in result.items)
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if applied
                    else ReconciliationState.CONFIRMED_NOT_APPLIED
                ),
                detail="employee creation checked by stable identifier",
            )
        if action.employee_id is None:
            return ReconciliationResult(
                action_id=action.action_id,
                state=ReconciliationState.UNKNOWN,
                detail="action has no stable employee target",
            )
        if action.function_id in {
            FunctionId.EMP_UPDATE_001,
            FunctionId.EMP_CONNECT_001,
            FunctionId.EMP_CONNECT_002,
            FunctionId.EMP_INVITE_001,
            FunctionId.EMP_INVITE_002,
            FunctionId.EMP_STATUS_001,
            FunctionId.EMP_STATUS_002,
            FunctionId.EMP_DELETE_001,
        }:
            return await self._reconcile_summary(action)
        if action.function_id is FunctionId.EMP_CONTRACT_002:
            contract_id = action.parameters.get("contract_id")
            if not isinstance(contract_id, str):
                state = ReconciliationState.UNKNOWN
            else:
                contract_records = await self._contracts.read(action.employee_id)
                state = (
                    ReconciliationState.CONFIRMED_APPLIED
                    if any(record.contract_id == contract_id for record in contract_records)
                    else ReconciliationState.CONFIRMED_NOT_APPLIED
                )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="contract reconciliation requires a stable contract identifier",
            )
        if action.function_id is FunctionId.EMP_MAT_002:
            category = action.parameters.get("category")
            maturation_records = await self._maturations.read(action.employee_id)
            applied = isinstance(category, str) and any(
                record.category.casefold() == category.casefold() for record in maturation_records
            )
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if applied
                    else ReconciliationState.CONFIRMED_NOT_APPLIED
                ),
                detail="maturation postcondition compared by category",
            )
        if action.function_id is FunctionId.EMP_RBAC_002:
            current = await self._roles.read_time_access(action.employee_id)
            values = current.model_dump(exclude={"employee_id"})
            comparable = {
                key: expected
                for key, expected in action.parameters.items()
                if key in values and isinstance(expected, bool)
            }
            if not comparable:
                state = ReconciliationState.UNKNOWN
            else:
                state = (
                    ReconciliationState.CONFIRMED_APPLIED
                    if all(values[key] == expected for key, expected in comparable.items())
                    else ReconciliationState.CONFIRMED_NOT_APPLIED
                )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="role postcondition compared against known controls",
            )
        if action.function_id in {FunctionId.EMP_DOC_004, FunctionId.EMP_DOC_005}:
            document_id = action.parameters.get("document_id")
            if not isinstance(document_id, str):
                state = ReconciliationState.UNKNOWN
            else:
                document_records = await self._documents.read(action.employee_id, DocumentQuery())
                present = any(record.document_id == document_id for record in document_records)
                expected_present = action.function_id is FunctionId.EMP_DOC_004
                state = (
                    ReconciliationState.CONFIRMED_APPLIED
                    if present is expected_present
                    else ReconciliationState.CONFIRMED_NOT_APPLIED
                )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="document metadata postcondition checked by stable identifier",
            )
        return ReconciliationResult(
            action_id=action.action_id,
            state=ReconciliationState.UNKNOWN,
            detail="no safe automated postcondition exists for this function",
        )

    async def reconcile(self, action: PreparedAction) -> ReconciliationResult:
        self._ensure_open()
        return await self._coordinator.run_reconciliation(
            "dic-browser", lambda: self._reconcile_direct(action)
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._coordinator.close()


PlaywrightDipendentiInCloudAdapter = PlaywrightDicAdapter

__all__ = ["PlaywrightDicAdapter", "PlaywrightDipendentiInCloudAdapter"]
