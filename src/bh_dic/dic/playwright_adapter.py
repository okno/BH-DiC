"""Async Playwright implementation of the deterministic DIC adapter protocol."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from pydantic import JsonValue

from bh_dic.dic.auth import (
    DicAuthOutcomeUnknownError,
    DicAuthStage,
    PlaywrightAuthenticator,
)
from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicAuthenticationError,
    DicConfigurationError,
    DicError,
    DicNotFoundError,
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
    EmployeeListQuery,
    EmployeeListResult,
    EmployeeState,
    EmployeeSummary,
    ExecutionResult,
    FunctionId,
    HealthStatus,
    MaturationRecord,
    OpaqueStateDigest,
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
    BaseDicPage,
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
    VerifiedUploadPayload,
)
from bh_dic.dic.values import canonical_decimal_text
from bh_dic.services.browser_runtime import BrowserCoordinator

T = TypeVar("T")

_DOCUMENT_EXECUTION_ONLY_PARAMETERS = frozenset(
    {"safe_local_path", "safe_local_sha256", "safe_local_size", "detected_mime"}
)
_DOCUMENT_UPLOAD_SUFFIXES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
_LIVE_WRITE_NOT_VERIFIABLE = frozenset(
    {
        FunctionId.EMP_INVITE_001,
        FunctionId.EMP_CONTRACT_003,
        FunctionId.EMP_DOC_003,
        FunctionId.EMP_DOC_005,
        FunctionId.EMP_EXPORT_001,
    }
)
_LIVE_CREATE_VERIFIABLE_FIELDS = frozenset(
    {
        "creation_mode",
        "first_name",
        "last_name",
        "payroll_number",
        "tax_code",
        "job_title",
        "business_email",
        "workplace",
    }
)
_SUMMARY_STATE_FUNCTIONS = frozenset(
    {
        FunctionId.EMP_UPDATE_001,
        FunctionId.EMP_CONNECT_001,
        FunctionId.EMP_CONNECT_002,
        FunctionId.EMP_INVITE_001,
        FunctionId.EMP_INVITE_002,
        FunctionId.EMP_STATUS_001,
        FunctionId.EMP_STATUS_002,
    }
)


@dataclass(frozen=True, slots=True)
class _WriteBaseline:
    stable_ids: frozenset[str]
    digest: OpaqueStateDigest | None = None


class PlaywrightDicAdapter:
    """No generic navigation/click API is exposed outside this class."""

    def __init__(
        self,
        page: PageLike,
        *,
        base_url: str = "https://secure.dipendentincloud.it",
        coordinator: BrowserCoordinator | None = None,
        expected_tenant_id: str | None = None,
        login_timeout_ms: float = 15_000,
        quarantine_root: Path | None = None,
        live_writes_enabled: bool = False,
        state_digest_key: bytes | None = None,
        verified_session_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if live_writes_enabled and expected_tenant_id is None:
            raise DicWriteDisabledError("live writes require an explicitly configured DIC tenant")
        self._coordinator = coordinator or BrowserCoordinator()
        self._live_writes_enabled = live_writes_enabled
        self._quarantine_root = quarantine_root.resolve() if quarantine_root else None
        if state_digest_key is not None and len(state_digest_key) < 32:
            raise DicConfigurationError("DIC state digest key must contain at least 32 bytes")
        self._state_digest_key = bytes(state_digest_key) if state_digest_key is not None else None
        self._verified_session_callback = verified_session_callback
        self._auth = PlaywrightAuthenticator(
            page,
            base_url,
            expected_tenant_id=expected_tenant_id,
            login_timeout_ms=login_timeout_ms,
        )
        self._auth_timeout_seconds = self._auth.login_timeout_seconds + 5
        self._employees = EmployeesListPage(
            page,
            base_url,
            expected_tenant_id=expected_tenant_id,
        )
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
            result = await operation()
            await self._notify_verified_session()
            return result

        return await self._coordinator.run_read(name, "dic-browser", authenticated_operation)

    async def _notify_verified_session(self) -> None:
        callback = self._verified_session_callback
        if callback is not None:
            await callback()

    async def _session_status(self, *, notify_verified: bool) -> SessionStatus:
        async def status_operation() -> SessionStatus:
            status = await self._auth.status()
            if notify_verified and status.state is SessionState.AUTHENTICATED:
                await self._notify_verified_session()
            return status

        return await self._coordinator.run_once(
            "session_status",
            "dic-browser",
            status_operation,
            timeout_seconds=self._auth_timeout_seconds,
        )

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
        except (DicError, TimeoutError):
            return HealthStatus(
                ready=False,
                authenticated=False,
                browser_available=True,
                detail="browser ready; authenticated tenant is unavailable",
            )
        authenticated = status.state is SessionState.AUTHENTICATED
        return HealthStatus(
            ready=authenticated,
            authenticated=authenticated,
            browser_available=True,
            detail=(
                "Playwright adapter ready"
                if authenticated
                else "browser ready; authenticated tenant is unavailable"
            ),
        )

    async def ensure_authenticated(
        self, credentials: DicCredentials | None = None
    ) -> SessionStatus:
        self._ensure_open()
        # Explicit authentication has its own mandatory persistence step in the
        # composition root. Avoid notifying the passive-session callback here so
        # a single operator authentication never races or writes the vault twice.
        current = await self._session_status(notify_verified=False)
        if current.state is SessionState.AUTHENTICATED:
            return current
        if credentials is None:
            raise DicAuthenticationError("DIC credentials are required for a new session")
        cancelled = False
        authenticated: SessionStatus | None = None
        try:
            authenticated = await self._coordinator.run_once(
                "authenticate",
                "dic-browser",
                lambda: self._auth.authenticate(credentials),
                timeout_seconds=self._auth_timeout_seconds,
            )
        except asyncio.CancelledError:
            cancelled = True
        if cancelled:
            raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
        if authenticated is None:
            raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
        return authenticated

    async def session_status(self) -> SessionStatus:
        self._ensure_open()
        return await self._session_status(notify_verified=True)

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

    async def get_balance_correction_state(
        self, employee_id: str, year: int, month: int, category: str
    ) -> BalanceCorrectionState:
        return await self._read(
            "employees.balance_correction_state",
            lambda: self._balance.read_correction_state(employee_id, year, month, category),
        )

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

    @staticmethod
    def _state_scope(
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
    ) -> str:
        clean_parameters = {
            name: value
            for name, value in parameters.items()
            if name not in _DOCUMENT_EXECUTION_ONLY_PARAMETERS
        }
        return json.dumps(
            {
                "employee_id": employee_id,
                "function_id": function_id.value,
                "parameters": clean_parameters,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def _state_digest_direct(
        self,
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
    ) -> OpaqueStateDigest:
        key = self._state_digest_key
        if key is None:
            raise DicConfigurationError("DIC state digest key is not configured")
        clean_parameters = {
            name: value
            for name, value in parameters.items()
            if name not in _DOCUMENT_EXECUTION_ONLY_PARAMETERS
        }
        scope = self._state_scope(function_id, employee_id, clean_parameters)
        page: BaseDicPage

        if function_id is FunctionId.EMP_CREATE_001:
            if set(clean_parameters).difference(_LIVE_CREATE_VERIFIABLE_FIELDS):
                raise DicWriteDisabledError(
                    "live employee create cannot verify every requested field"
                )
            await self._employees.stable_employee_ids_for_create(dict(clean_parameters))
            page = self._employees
        elif function_id is FunctionId.EMP_EXPORT_001:
            await self._employees.list(
                EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page_size=100)
            )
            page = self._employees
        elif function_id is FunctionId.EMP_DELETE_001:
            if employee_id is None:
                raise DicValidationError("employee delete state requires employee_id")
            await self._employees.list(
                EmployeeListQuery(
                    query=employee_id,
                    employee_filter=EmployeeFilter.ALL,
                    page_size=100,
                )
            )
            page = self._employees
        elif function_id in _SUMMARY_STATE_FUNCTIONS:
            if employee_id is None:
                raise DicValidationError("employee state requires employee_id")
            if function_id is FunctionId.EMP_UPDATE_001:
                verified = await self._summary.verify_expected(employee_id, dict(clean_parameters))
                if verified is None:
                    raise DicValidationError("employee update state cannot be verified")
                if verified:
                    raise DicValidationError("employee update would not change state")
            else:
                await self._summary.open(employee_id)
            page = self._summary
        elif function_id in {FunctionId.EMP_CONTRACT_002, FunctionId.EMP_CONTRACT_003}:
            if employee_id is None:
                raise DicValidationError("contract state requires employee_id")
            contract_records = await self._contracts.read(employee_id)
            contract_id = clean_parameters.get("contract_id")
            if contract_id is not None:
                if not isinstance(contract_id, str):
                    raise DicValidationError("contract_id must be a string")
                contract_matches = [
                    record for record in contract_records if record.contract_id == contract_id
                ]
                if len(contract_matches) != 1:
                    raise DicNotFoundError("contract target is not uniquely present")
                if not contract_matches[0].stable_identifier or not contract_matches[0].actionable:
                    raise DicValidationError(
                        "contract target lacks a stable actionable DOM identifier"
                    )
                if function_id is FunctionId.EMP_CONTRACT_002:
                    verified = await self._contracts.verify_expected(
                        employee_id,
                        contract_id,
                        dict(clean_parameters),
                    )
                    if verified is None:
                        raise DicValidationError("contract update state cannot be verified")
                    if verified:
                        raise DicValidationError("contract update would not change state")
            page = self._contracts
        elif function_id is FunctionId.EMP_MAT_002:
            if employee_id is None:
                raise DicValidationError("maturation state requires employee_id")
            await self._maturations.read(employee_id)
            page = self._maturations
        elif function_id is FunctionId.EMP_BAL_002:
            if employee_id is None:
                raise DicValidationError("balance state requires employee_id")
            year = clean_parameters.get("year")
            month = clean_parameters.get("month")
            category = clean_parameters.get("category")
            if (
                not isinstance(year, int)
                or isinstance(year, bool)
                or not isinstance(month, int)
                or isinstance(month, bool)
                or not isinstance(category, str)
            ):
                raise DicValidationError("balance state target is incomplete")
            balance_state = await self._balance.read_correction_state(
                employee_id, year, month, category
            )
            try:
                expected_previous = canonical_decimal_text(clean_parameters.get("previous_value"))
                expected_amount = canonical_decimal_text(clean_parameters.get("amount"))
            except ValueError as exc:
                raise DicValidationError("balance state parameters are incomplete") from exc
            if balance_state.current_value != expected_previous:
                raise DicValidationError("balance correction precondition changed")
            if balance_state.current_value == expected_amount:
                raise DicValidationError("balance correction would not change state")
            page = self._balance
        elif function_id is FunctionId.EMP_RBAC_002:
            if employee_id is None:
                raise DicValidationError("role state requires employee_id")
            if set(clean_parameters) != {"role_name", "enabled"}:
                raise DicValidationError("role update requires only role_name and enabled")
            role_name = clean_parameters.get("role_name")
            enabled = clean_parameters.get("enabled")
            if (
                not isinstance(role_name, str)
                or not role_name.strip()
                or not isinstance(enabled, bool)
            ):
                raise DicValidationError("role update state is invalid")
            current_roles = await self._roles.read_roles(employee_id)
            role_matches = [
                role
                for role in current_roles.roles
                if role.name.casefold() == role_name.strip().casefold()
            ]
            if len(role_matches) != 1:
                raise DicValidationError("role target is not uniquely present")
            if role_matches[0].enabled is enabled:
                raise DicValidationError("role update would not change state")
            page = self._roles
        elif function_id in {
            FunctionId.EMP_DOC_002,
            FunctionId.EMP_DOC_003,
            FunctionId.EMP_DOC_004,
            FunctionId.EMP_DOC_005,
        }:
            if employee_id is None:
                raise DicValidationError("document state requires employee_id")
            document_records = await self._documents.read(employee_id, DocumentQuery())
            document_id = clean_parameters.get("document_id")
            if document_id is not None:
                if not isinstance(document_id, str):
                    raise DicValidationError("document_id must be a string")
                document_matches = [
                    record for record in document_records if record.document_id == document_id
                ]
                if len(document_matches) != 1:
                    raise DicNotFoundError("document target is not uniquely present")
                if not document_matches[0].stable_identifier:
                    raise DicValidationError("document target lacks a stable DOM identifier")
                if (
                    function_id in {FunctionId.EMP_DOC_004, FunctionId.EMP_DOC_005}
                    and not document_matches[0].actionable
                ):
                    raise DicValidationError("document target is not actionable in the DOM")
                if function_id is FunctionId.EMP_DOC_004:
                    verified = await self._documents.verify_expected_metadata(
                        employee_id,
                        document_id,
                        dict(clean_parameters),
                    )
                    if verified is None:
                        raise DicValidationError("document update state cannot be verified")
                    if verified:
                        raise DicValidationError("document update would not change state")
            page = self._documents
        else:
            raise DicValidationError("function has no mutation state digest plan")
        return await page.opaque_state_digest(key, scope=scope)

    async def get_state_digest(
        self,
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
    ) -> OpaqueStateDigest:
        """Return only a keyed digest of the raw resource state used for CAS."""

        return await self._read(
            "state.digest",
            lambda: self._state_digest_direct(function_id, employee_id, parameters),
        )

    async def _capture_write_baseline(self, action: PreparedAction) -> _WriteBaseline | None:
        """Capture only stable IDs and an optional opaque digest before dispatch."""

        employee_id = action.employee_id
        if action.function_id is FunctionId.EMP_CREATE_001:
            unsupported = set(action.parameters).difference(_LIVE_CREATE_VERIFIABLE_FIELDS)
            if unsupported:
                raise DicWriteDisabledError(
                    "live employee create cannot verify every requested field"
                )
            return _WriteBaseline(
                stable_ids=await self._employees.stable_employee_ids_for_create(
                    dict(action.parameters)
                )
            )
        if action.function_id in _SUMMARY_STATE_FUNCTIONS:
            if employee_id is None:
                raise DicValidationError("summary write requires employee_id")
            key = self._state_digest_key
            if key is None:
                raise DicConfigurationError("DIC state digest key is not configured")
            if action.function_id is FunctionId.EMP_UPDATE_001:
                verified = await self._summary.verify_expected(employee_id, dict(action.parameters))
                if verified is None:
                    raise DicValidationError("employee update state cannot be verified")
                if verified:
                    raise DicValidationError("employee update would not change state")
            else:
                await self._summary.open(employee_id)
            return _WriteBaseline(
                stable_ids=frozenset({employee_id}),
                digest=await self._summary.opaque_state_digest(
                    key,
                    scope=self._state_scope(action.function_id, employee_id, action.parameters),
                ),
            )
        if action.function_id is FunctionId.EMP_DELETE_001:
            if employee_id is None:
                raise DicValidationError("employee delete requires employee_id")
            result = await self._employees.list(
                EmployeeListQuery(
                    query=employee_id,
                    employee_filter=EmployeeFilter.ALL,
                    page_size=100,
                )
            )
            stable_ids = frozenset(item.employee_id for item in result.items)
            if employee_id not in stable_ids:
                raise DicValidationError(
                    "employee delete target is not present by stable identifier"
                )
            return _WriteBaseline(stable_ids=stable_ids)
        if action.function_id in {FunctionId.EMP_CONTRACT_002, FunctionId.EMP_CONTRACT_003}:
            if employee_id is None:
                raise DicValidationError("contract write requires employee_id")
            stable_ids = await self._contracts.stable_contract_ids(employee_id)
            contract_id = action.parameters.get("contract_id")
            if contract_id is not None and (
                not isinstance(contract_id, str) or contract_id not in stable_ids
            ):
                raise DicValidationError("contract target is not present by stable DOM identifier")
            digest = None
            if action.function_id is FunctionId.EMP_CONTRACT_002 and contract_id is not None:
                verified = await self._contracts.verify_expected(
                    employee_id,
                    contract_id,
                    dict(action.parameters),
                )
                if verified is None:
                    raise DicValidationError("contract update state cannot be verified")
                if verified:
                    raise DicValidationError("contract update would not change state")
                key = self._state_digest_key
                if key is None:
                    raise DicConfigurationError("DIC state digest key is not configured")
                digest = await self._contracts.opaque_state_digest(
                    key,
                    scope=self._state_scope(action.function_id, employee_id, action.parameters),
                )
            return _WriteBaseline(stable_ids=stable_ids, digest=digest)
        if action.function_id is FunctionId.EMP_MAT_002:
            if employee_id is None:
                raise DicValidationError("maturation write requires employee_id")
            return _WriteBaseline(
                stable_ids=await self._maturations.stable_maturation_ids(employee_id)
            )
        if action.function_id in {
            FunctionId.EMP_DOC_002,
            FunctionId.EMP_DOC_004,
            FunctionId.EMP_DOC_005,
        }:
            if employee_id is None:
                raise DicValidationError("document write requires employee_id")
            stable_ids = await self._documents.stable_document_ids(employee_id)
            document_id = action.parameters.get("document_id")
            if document_id is not None and (
                not isinstance(document_id, str) or document_id not in stable_ids
            ):
                raise DicValidationError("document target is not present by stable DOM identifier")
            digest = None
            if action.function_id is FunctionId.EMP_DOC_004:
                key = self._state_digest_key
                if key is None:
                    raise DicConfigurationError("DIC state digest key is not configured")
                digest = await self._documents.opaque_state_digest(
                    key,
                    scope=self._state_scope(action.function_id, employee_id, action.parameters),
                )
            return _WriteBaseline(stable_ids=stable_ids, digest=digest)
        return None

    async def _dispatch_write(self, action: PreparedAction) -> None:
        if action.function_id in _LIVE_WRITE_NOT_VERIFIABLE:
            raise DicWriteDisabledError(
                "live write has no exhaustive observable postcondition and is unavailable"
            )
        if action.function_id is FunctionId.EMP_CREATE_001:
            await self._employees.create_employee(action)
        elif action.function_id in _SUMMARY_STATE_FUNCTIONS | {FunctionId.EMP_DELETE_001}:
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
                sanitized, verified_upload = await self._validated_document_upload_action(action)
                await self._documents.execute(sanitized, verified_upload=verified_upload)
            else:
                await self._documents.execute(action)
        else:
            raise DicValidationError("function has no deterministic write plan")

    @staticmethod
    def _load_verified_upload(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        detected_mime: str,
    ) -> VerifiedUploadPayload:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise DicValidationError("quarantined upload is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise DicValidationError("quarantined upload changed while being read")
        content = b"".join(chunks)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if len(content) != expected_size or not hmac.compare_digest(actual_sha256, expected_sha256):
            raise DicValidationError("quarantined upload integrity changed before dispatch")
        try:
            suffix = _DOCUMENT_UPLOAD_SUFFIXES[detected_mime]
        except KeyError as exc:
            raise DicValidationError("document upload MIME type is not supported") from exc
        return VerifiedUploadPayload(
            name=f"document-upload{suffix}",
            mime_type=detected_mime,
            buffer=content,
        )

    async def _validated_document_upload_action(
        self, action: PreparedAction
    ) -> tuple[PreparedAction, VerifiedUploadPayload]:
        if self._quarantine_root is None:
            raise DicWriteDisabledError("document upload quarantine root is not configured")
        raw_path = action.parameters.get("safe_local_path")
        expected_sha256 = action.parameters.get("safe_local_sha256")
        expected_size = action.parameters.get("safe_local_size")
        detected_mime = action.parameters.get("detected_mime")
        if not isinstance(raw_path, str):
            raise DicValidationError("document upload requires a quarantined local path")
        if (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        ):
            raise DicValidationError("document upload requires a valid expected SHA-256")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise DicValidationError("document upload requires a valid expected size")
        if (
            not isinstance(detected_mime, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", detected_mime) is None
        ):
            raise DicValidationError("document upload requires a detected MIME type")
        try:
            resolved = await asyncio.to_thread(Path(raw_path).resolve, True)
        except OSError:
            raise DicValidationError("quarantined upload file is unavailable") from None
        if not resolved.is_relative_to(self._quarantine_root):
            raise DicValidationError("document upload path is outside quarantine")
        try:
            verified_upload = await asyncio.to_thread(
                self._load_verified_upload,
                resolved,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                detected_mime=detected_mime,
            )
        except OSError:
            raise DicValidationError("quarantined upload file is unavailable") from None
        sanitized_parameters = {
            name: value
            for name, value in action.parameters.items()
            if name in {"category", "expiry_date"}
        }
        return action.model_copy(update={"parameters": sanitized_parameters}), verified_upload

    async def execute_prepared(self, action: PreparedAction) -> ExecutionResult:
        self._ensure_open()
        if not self._live_writes_enabled:
            raise DicWriteDisabledError("Playwright live writes are disabled at adapter boundary")
        if action.function_id in _LIVE_WRITE_NOT_VERIFIABLE:
            raise DicWriteDisabledError(
                "live write has no exhaustive observable postcondition and is unavailable"
            )
        baseline = await self._read("write.baseline", lambda: self._capture_write_baseline(action))

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
        except (TimeoutError, ConnectionError, PlaywrightError, DicUiChangedError) as exc:
            ambiguous = DicAmbiguousWriteOutcomeError(
                "write failed after dispatch; automatic retry is prohibited"
            )
            outcome = await self._reconcile_after_dispatch(action, baseline)
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
        outcome = await self._reconcile_after_dispatch(action, baseline)
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

    async def _reconcile_after_dispatch(
        self, action: PreparedAction, baseline: _WriteBaseline | None
    ) -> ReconciliationResult:
        """Convert every post-dispatch read failure into an explicit unknown outcome."""

        try:
            if baseline is None:
                return await self.reconcile(action)
            return await self._coordinator.run_reconciliation(
                "dic-browser",
                lambda: self._reconcile_direct(action, baseline=baseline),
            )
        except Exception as exc:
            raise DicReconciliationRequiredError(
                "post-dispatch reconciliation failed; write outcome is unknown"
            ) from exc

    @staticmethod
    def _compare_expected(actual: object, expected: object) -> bool:
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.strip().casefold() == expected.strip().casefold()
        return actual == expected

    async def _reconcile_summary(
        self, action: PreparedAction, baseline: _WriteBaseline | None
    ) -> ReconciliationResult:
        if action.employee_id is None:
            raise DicValidationError("summary reconciliation requires employee_id")
        if baseline is None or baseline.digest is None:
            return ReconciliationResult(
                action_id=action.action_id,
                state=ReconciliationState.UNKNOWN,
                detail="summary write has no verified pre-dispatch baseline",
            )
        if action.function_id is FunctionId.EMP_INVITE_001:
            return ReconciliationResult(
                action_id=action.action_id,
                state=ReconciliationState.UNKNOWN,
                detail="invite resend has no observable event proving delivery",
            )
        if action.function_id is FunctionId.EMP_STATUS_001:
            summary = await self._summary.read(action.employee_id)
            applied = summary.state is EmployeeState.INACTIVE
        elif action.function_id is FunctionId.EMP_STATUS_002:
            summary = await self._summary.read(action.employee_id)
            applied = summary.state is EmployeeState.ACTIVE
        elif action.function_id is FunctionId.EMP_UPDATE_001:
            verified = await self._summary.verify_expected(
                action.employee_id, dict(action.parameters)
            )
            if verified is not True:
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="employee update values could not be verified exactly",
                )
            applied = True
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
            await self._summary.open(action.employee_id)
        if applied:
            current_digest = await self._current_page_digest(self._summary, action)
            if current_digest == baseline.digest:
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="summary value matches but no state transition was observed",
                )
        return ReconciliationResult(
            action_id=action.action_id,
            state=(
                ReconciliationState.CONFIRMED_APPLIED
                if applied
                else ReconciliationState.CONFIRMED_NOT_APPLIED
            ),
            detail="summary postcondition compared against live read",
        )

    @staticmethod
    def _verified_state(verified: bool | None) -> ReconciliationState:
        if verified is None:
            return ReconciliationState.UNKNOWN
        if verified:
            return ReconciliationState.CONFIRMED_APPLIED
        return ReconciliationState.CONFIRMED_NOT_APPLIED

    async def _current_page_digest(
        self, page: BaseDicPage, action: PreparedAction
    ) -> OpaqueStateDigest:
        key = self._state_digest_key
        if key is None:
            raise DicConfigurationError("DIC state digest key is not configured")
        return await page.opaque_state_digest(
            key,
            scope=self._state_scope(action.function_id, action.employee_id, action.parameters),
        )

    async def _reconcile_direct(
        self,
        action: PreparedAction,
        *,
        baseline: _WriteBaseline | None = None,
    ) -> ReconciliationResult:
        if action.function_id is FunctionId.EMP_CREATE_001:
            verified = (
                None
                if baseline is None
                else await self._employees.verify_created_employee(
                    baseline.stable_ids, dict(action.parameters)
                )
            )
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if verified is True
                    else ReconciliationState.UNKNOWN
                ),
                detail="employee creation checked by one new stable ID and exact values",
            )
        if action.employee_id is None:
            return ReconciliationResult(
                action_id=action.action_id,
                state=ReconciliationState.UNKNOWN,
                detail="action has no stable employee target",
            )
        if action.function_id is FunctionId.EMP_DELETE_001:
            if baseline is None or action.employee_id not in baseline.stable_ids:
                return ReconciliationResult(
                    action_id=action.action_id,
                    state=ReconciliationState.UNKNOWN,
                    detail="employee delete has no verified pre-dispatch baseline",
                )
            try:
                result = await self._employees.list(
                    EmployeeListQuery(
                        query=action.employee_id,
                        employee_filter=EmployeeFilter.ALL,
                        page_size=100,
                    )
                )
            except Exception:
                state = ReconciliationState.UNKNOWN
                detail = "employee list could not prove delete outcome"
            else:
                present = any(item.employee_id == action.employee_id for item in result.items)
                if present:
                    state = ReconciliationState.CONFIRMED_NOT_APPLIED
                    detail = "employee remains present by exact stable identifier"
                elif result.has_next:
                    state = ReconciliationState.UNKNOWN
                    detail = "employee search result was not exhaustive"
                else:
                    state = ReconciliationState.CONFIRMED_APPLIED
                    detail = "employee absence verified by exact list search"
            return ReconciliationResult(action_id=action.action_id, state=state, detail=detail)
        if action.function_id in {
            FunctionId.EMP_UPDATE_001,
            FunctionId.EMP_CONNECT_001,
            FunctionId.EMP_CONNECT_002,
            FunctionId.EMP_INVITE_001,
            FunctionId.EMP_INVITE_002,
            FunctionId.EMP_STATUS_001,
            FunctionId.EMP_STATUS_002,
        }:
            return await self._reconcile_summary(action, baseline)
        if action.function_id is FunctionId.EMP_CONTRACT_002:
            contract_id = action.parameters.get("contract_id")
            if baseline is None:
                verified = None
            elif isinstance(contract_id, str):
                verified = await self._contracts.verify_expected(
                    action.employee_id,
                    contract_id,
                    dict(action.parameters),
                )
                if verified is True:
                    current_digest = await self._current_page_digest(self._contracts, action)
                    verified = baseline.digest is not None and current_digest != baseline.digest
            else:
                verified = await self._contracts.verify_created_contract(
                    action.employee_id,
                    baseline.stable_ids,
                    dict(action.parameters),
                )
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if verified is True
                    else ReconciliationState.UNKNOWN
                ),
                detail="contract transition and exact expected values were compared",
            )
        if action.function_id is FunctionId.EMP_CONTRACT_003:
            contract_id = action.parameters.get("contract_id")
            if (
                not isinstance(contract_id, str)
                or baseline is None
                or contract_id not in baseline.stable_ids
            ):
                state = ReconciliationState.UNKNOWN
            else:
                contract_records = await self._contracts.read(action.employee_id)
                matching = [
                    record for record in contract_records if record.contract_id == contract_id
                ]
                if any(not record.stable_identifier for record in matching):
                    state = ReconciliationState.UNKNOWN
                else:
                    state = (
                        ReconciliationState.CONFIRMED_NOT_APPLIED
                        if matching
                        else ReconciliationState.UNKNOWN
                    )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="contract deletion checked by stable identifier",
            )
        if action.function_id is FunctionId.EMP_MAT_002:
            verified = (
                None
                if baseline is None
                else await self._maturations.verify_created_maturation(
                    action.employee_id,
                    baseline.stable_ids,
                    dict(action.parameters),
                )
            )
            state = (
                ReconciliationState.CONFIRMED_APPLIED
                if verified is True
                else ReconciliationState.UNKNOWN
            )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="maturation postcondition compared against category and validity",
            )
        if action.function_id is FunctionId.EMP_RBAC_002:
            role_name = action.parameters.get("role_name")
            enabled = action.parameters.get("enabled")
            if isinstance(role_name, str) and isinstance(enabled, bool):
                current_roles = await self._roles.read_roles(action.employee_id)
                matches = [
                    role
                    for role in current_roles.roles
                    if role.name.casefold() == role_name.casefold()
                ]
                if len(matches) != 1:
                    state = ReconciliationState.UNKNOWN
                else:
                    state = (
                        ReconciliationState.CONFIRMED_APPLIED
                        if matches[0].enabled is enabled
                        else ReconciliationState.CONFIRMED_NOT_APPLIED
                    )
            else:
                state = ReconciliationState.UNKNOWN
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="role postcondition compared against known controls",
            )
        if action.function_id is FunctionId.EMP_BAL_002:
            year = action.parameters.get("year")
            month = action.parameters.get("month")
            category = action.parameters.get("category")
            amount = action.parameters.get("amount")
            if (
                not isinstance(year, int)
                or isinstance(year, bool)
                or not isinstance(month, int)
                or isinstance(month, bool)
                or not isinstance(category, str)
            ):
                state = ReconciliationState.UNKNOWN
            else:
                try:
                    expected_amount = canonical_decimal_text(amount)
                    balance_state = await self._balance.read_correction_state(
                        action.employee_id, year, month, category
                    )
                except (ValueError, DicValidationError, DicUiChangedError):
                    state = ReconciliationState.UNKNOWN
                else:
                    state = (
                        ReconciliationState.CONFIRMED_APPLIED
                        if balance_state.current_value == expected_amount
                        else ReconciliationState.CONFIRMED_NOT_APPLIED
                    )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="balance correction checked by exact year, month and category",
            )
        if action.function_id is FunctionId.EMP_DOC_002:
            if baseline is None:
                verified = None
            else:
                verified = await self._documents.verify_uploaded_document(
                    action.employee_id,
                    baseline.stable_ids,
                    dict(action.parameters),
                )
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if verified is True
                    else ReconciliationState.UNKNOWN
                ),
                detail="document upload requires one new stable ID and exact metadata",
            )
        if action.function_id is FunctionId.EMP_DOC_004:
            document_id = action.parameters.get("document_id")
            if not isinstance(document_id, str):
                verified = None
            else:
                verified = await self._documents.verify_expected_metadata(
                    action.employee_id,
                    document_id,
                    dict(action.parameters),
                )
                if verified is True:
                    current_digest = await self._current_page_digest(self._documents, action)
                    verified = (
                        baseline is not None
                        and baseline.digest is not None
                        and current_digest != baseline.digest
                    )
            return ReconciliationResult(
                action_id=action.action_id,
                state=(
                    ReconciliationState.CONFIRMED_APPLIED
                    if verified is True
                    else ReconciliationState.UNKNOWN
                ),
                detail="document metadata transition matched exact expected values",
            )
        if action.function_id is FunctionId.EMP_DOC_005:
            document_id = action.parameters.get("document_id")
            if (
                not isinstance(document_id, str)
                or baseline is None
                or document_id not in baseline.stable_ids
            ):
                state = ReconciliationState.UNKNOWN
            else:
                document_records = await self._documents.read(action.employee_id, DocumentQuery())
                document_matches = [
                    record for record in document_records if record.document_id == document_id
                ]
                if any(not record.stable_identifier for record in document_matches):
                    state = ReconciliationState.UNKNOWN
                else:
                    state = (
                        ReconciliationState.CONFIRMED_NOT_APPLIED
                        if document_matches
                        else ReconciliationState.UNKNOWN
                    )
            return ReconciliationResult(
                action_id=action.action_id,
                state=state,
                detail="document deletion checked by stable DOM identifier",
            )
        return ReconciliationResult(
            action_id=action.action_id,
            state=ReconciliationState.UNKNOWN,
            detail="no safe automated postcondition exists for this function",
        )

    async def reconcile(self, action: PreparedAction) -> ReconciliationResult:
        self._ensure_open()
        try:
            return await self._coordinator.run_reconciliation(
                "dic-browser", lambda: self._reconcile_direct(action)
            )
        except DicReconciliationRequiredError:
            raise
        except Exception as exc:
            raise DicReconciliationRequiredError(
                "reconciliation failed; write outcome remains unknown"
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._coordinator.close()


PlaywrightDipendentiInCloudAdapter = PlaywrightDicAdapter

__all__ = ["PlaywrightDicAdapter", "PlaywrightDipendentiInCloudAdapter"]
