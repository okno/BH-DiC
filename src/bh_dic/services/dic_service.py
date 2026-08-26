"""Service boundary joining policy-approved actions to the DIC adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import JsonValue, TypeAdapter, ValidationError

from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.dic.errors import (
    DicApprovalError,
    DicInvalidPreparedActionError,
    DicValidationError,
    DicWriteDisabledError,
)
from bh_dic.dic.models import (
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
    EmployeeSummary,
    ExecutionResult,
    FunctionId,
    HealthStatus,
    MaturationRecord,
    NotificationListResult,
    PayrollMetadata,
    PreparedAction,
    PreviewChange,
    ReconciliationResult,
    RolesResult,
    SessionStatus,
    TimeAccessResult,
)
from bh_dic.dic.protocol import DipendentiInCloudAdapter
from bh_dic.policies.catalog import (
    FunctionSpec,
    WriteParameterValidationError,
    get_function_spec,
    validate_write_parameters,
)
from bh_dic.policies.feature_flags import FeatureFlags

_JSON_MAPPING = TypeAdapter(dict[str, JsonValue])
_SECRET_KEY = re.compile(r"(?:password|passwd|token|secret|cookie|api[_-]?key|totp)", re.I)
_DOCUMENT_EXECUTION_ONLY_PARAMETERS = frozenset(
    {"safe_local_path", "safe_local_sha256", "safe_local_size", "detected_mime"}
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PAYROLL_PRESENCE_MAX_EMPLOYEES = 500


@dataclass(frozen=True, slots=True)
class PayrollPresenceResult:
    """Result of a bounded, read-only payroll presence comparison."""

    year: int
    month: int
    scanned: int
    employees: tuple[EmployeeListItem, ...]


class DicService:
    """Feature gates writes again immediately before deterministic execution.

    ``ApprovalService.begin_execution`` must run first.  This service accepts an
    EXECUTING action only, preserving the existing approval state machine as the
    sole source of confirmation and separation-of-duty decisions.
    """

    def __init__(
        self,
        adapter: DipendentiInCloudAdapter,
        feature_flags: FeatureFlags,
        *,
        capabilities: frozenset[str] = frozenset(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.adapter = adapter
        self.feature_flags = feature_flags
        self.capabilities = capabilities
        self._clock = clock

    async def health(self) -> HealthStatus:
        return await self.adapter.health()

    async def ensure_authenticated(
        self, credentials: DicCredentials | None = None
    ) -> SessionStatus:
        return await self.adapter.ensure_authenticated(credentials)

    async def session_status(self) -> SessionStatus:
        return await self.adapter.session_status()

    def _require_enabled(self, function_id: str) -> None:
        spec = get_function_spec(function_id)
        if spec is None or not spec.is_write:
            raise DicValidationError("a known write Function ID is required")
        disabled = [flag for flag in spec.feature_flags if not self.feature_flags.enabled(flag)]
        if disabled:
            raise DicWriteDisabledError(
                f"write disabled by feature flag(s): {', '.join(sorted(disabled))}"
            )
        missing = spec.required_capabilities.difference(self.capabilities)
        if missing:
            raise DicWriteDisabledError(
                f"write disabled by unavailable capability: {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _validated_parameters(parameters: Mapping[str, Any]) -> dict[str, JsonValue]:
        forbidden = sorted(key for key in parameters if _SECRET_KEY.search(str(key)))
        if forbidden:
            raise DicValidationError(
                f"credential-like parameters are forbidden: {', '.join(forbidden)}"
            )
        try:
            return _JSON_MAPPING.validate_python(dict(parameters))
        except ValidationError as exc:
            raise DicValidationError("write parameters must be JSON-compatible") from exc

    @classmethod
    def _catalog_validated_parameters(
        cls,
        pending: PendingAction,
        spec: FunctionSpec,
        parameters: Mapping[str, Any],
    ) -> dict[str, JsonValue]:
        """Re-enforce the catalog at the final service boundary.

        DOC-002 is the sole exception to the persisted schema shape: ``upload_id``
        has already been consumed by the application and is replaced by four
        execution-only values derived from a claimed CLEAN server-side record.
        """

        json_parameters = cls._validated_parameters(parameters)
        candidate: dict[str, Any]
        if pending.function_id == "EMP-DOC-002":
            public_names = spec.operational_parameter_names - {"upload_id"}
            allowed = public_names | _DOCUMENT_EXECUTION_ONLY_PARAMETERS
            unexpected = set(json_parameters).difference(allowed)
            missing_internal = _DOCUMENT_EXECUTION_ONLY_PARAMETERS.difference(json_parameters)
            if unexpected or missing_internal or "upload_id" in json_parameters:
                raise DicValidationError("document execution parameters violate the closed schema")
            path = json_parameters.get("safe_local_path")
            sha256 = json_parameters.get("safe_local_sha256")
            size = json_parameters.get("safe_local_size")
            mime = json_parameters.get("detected_mime")
            if (
                not isinstance(path, str)
                or not path
                or not isinstance(sha256, str)
                or _SHA256.fullmatch(sha256) is None
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or not isinstance(mime, str)
                or not mime
                or len(mime) > 128
            ):
                raise DicValidationError("document execution capability is malformed")
            candidate = {
                name: value for name, value in json_parameters.items() if name in public_names
            }
            # The original opaque upload ID was schema-validated before persistence;
            # a fixed syntactic sentinel lets the same catalog validate only metadata.
            candidate["upload_id"] = "0" * 32
        else:
            candidate = dict(json_parameters)
        if pending.motivation is not None:
            candidate["motivation"] = pending.motivation
        try:
            normalized = validate_write_parameters(spec, candidate)
        except WriteParameterValidationError as exc:
            raise DicValidationError("write parameters violate the policy catalog") from exc
        normalized.pop("motivation", None)
        normalized.pop("upload_id", None)
        if pending.function_id == "EMP-DOC-002":
            normalized.update(
                {name: json_parameters[name] for name in _DOCUMENT_EXECUTION_ONLY_PARAMETERS}
            )
        return cls._validated_parameters(normalized)

    @staticmethod
    def _fingerprint(
        pending: PendingAction, function_id: FunctionId, parameters: dict[str, JsonValue]
    ) -> str:
        canonical = json.dumps(
            {
                "action_id": pending.action_id,
                "correlation_id": pending.correlation_id,
                "employee_id": pending.target_employee_id,
                "function_id": function_id.value,
                "idempotency_key": pending.idempotency_key,
                "parameters": parameters,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _redacted_text(value: object | None) -> str | None:
        if value is None:
            return None
        rendered = str(value)
        return rendered[:509] + "..." if len(rendered) > 512 else rendered

    @classmethod
    def _preview(cls, pending: PendingAction) -> tuple[PreviewChange, ...]:
        preview: list[PreviewChange] = []
        for field in sorted(pending.redacted_diff):
            value = pending.redacted_diff[field]
            if _SECRET_KEY.search(str(field)):
                preview.append(
                    PreviewChange(
                        field=str(field)[:128],
                        before_redacted="[REDACTED]",
                        after_redacted="[REDACTED]",
                    )
                )
                continue
            before: object | None = None
            after: object | None = value
            if isinstance(value, Mapping):
                before = value.get("before")
                after = value.get("after")
            preview.append(
                PreviewChange(
                    field=str(field)[:128],
                    before_redacted=cls._redacted_text(before),
                    after_redacted=cls._redacted_text(after),
                )
            )
        return tuple(preview)

    def _build_prepared(
        self,
        pending: PendingAction,
        parameters: Mapping[str, Any],
        *,
        for_execution: bool,
    ) -> PreparedAction:
        if for_execution:
            self._require_enabled(pending.function_id)
            if pending.status is not ActionStatus.EXECUTING:
                raise DicApprovalError("action must be atomically claimed as EXECUTING first")
            if pending.expires_at <= self._clock():
                raise DicApprovalError("approved action is expired")
        elif pending.status not in {
            ActionStatus.EXECUTING,
            ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION,
        }:
            raise DicApprovalError("action is not eligible for reconciliation")
        if not pending.confirmed:
            raise DicApprovalError("action confirmation has not been consumed")
        if len(pending.approved_by) < pending.approvals_required:
            raise DicApprovalError("action does not have enough distinct approvers")
        try:
            function_id = FunctionId(pending.function_id)
        except ValueError as exc:
            raise DicValidationError("unknown DIC Function ID") from exc
        spec = get_function_spec(pending.function_id)
        if spec is None:
            raise DicValidationError("unknown DIC Function ID")
        if pending.approvals_required != spec.approvals_required:
            raise DicInvalidPreparedActionError("approval requirement differs from policy catalog")
        if spec.requires_target and pending.target_employee_id is None:
            raise DicInvalidPreparedActionError("approved action has no stable employee target")
        validated = self._catalog_validated_parameters(pending, spec, parameters)
        return PreparedAction(
            action_id=pending.action_id,
            function_id=function_id,
            employee_id=pending.target_employee_id,
            parameters=validated,
            idempotency_key=pending.idempotency_key,
            correlation_id=pending.correlation_id,
            request_fingerprint=self._fingerprint(pending, function_id, validated),
            preview=self._preview(pending),
            required_approvals=pending.approvals_required,
            created_at=pending.created_at,
            expires_at=pending.expires_at,
        )

    def prepare_execution(
        self, pending: PendingAction, parameters: Mapping[str, Any]
    ) -> PreparedAction:
        """Translate one claimed approval action without changing its identity."""

        return self._build_prepared(pending, parameters, for_execution=True)

    async def execute(
        self, pending: PendingAction, parameters: Mapping[str, Any]
    ) -> ExecutionResult:
        """Execute exactly once; ambiguous writes are never retried here."""

        prepared = self.prepare_execution(pending, parameters)
        return await self.adapter.execute_prepared(prepared)

    async def reconcile(
        self, pending: PendingAction, parameters: Mapping[str, Any]
    ) -> ReconciliationResult:
        # Reconciliation is read-only and remains available after the kill switch
        # is activated or the original action TTL has elapsed.
        prepared = self._build_prepared(pending, parameters, for_execution=False)
        return await self.adapter.reconcile(prepared)

    async def list_employees(self, query: EmployeeListQuery) -> EmployeeListResult:
        return await self.adapter.list_employees(query)

    async def list_all_employees(
        self,
        query: EmployeeListQuery,
        *,
        max_pages: int = 500,
        max_records: int = 50_000,
    ) -> EmployeeListResult:
        """Read every page and fail closed on drift, duplicates, or incomplete pagination."""

        if max_pages < 1 or max_records < 1:
            raise ValueError("employee pagination bounds must be positive")
        base = query.model_copy(update={"page": 1, "page_size": 100})
        page = 1
        expected_total: int | None = None
        items: list[EmployeeListItem] = []
        seen: set[str] = set()
        while True:
            current = await self.adapter.list_employees(base.model_copy(update={"page": page}))
            if current.page != page:
                raise DicValidationError("employee pagination returned an unexpected page")
            if expected_total is None:
                expected_total = current.total
                if expected_total > max_records:
                    raise DicValidationError("employee result exceeds the configured safety limit")
            elif current.total != expected_total:
                raise DicValidationError("employee total changed while reading all pages")
            for item in current.items:
                if item.employee_id in seen:
                    raise DicValidationError("employee pagination returned a duplicate identifier")
                seen.add(item.employee_id)
                items.append(item)
            if len(items) > max_records:
                raise DicValidationError("employee result exceeds the configured safety limit")
            if not current.has_next:
                break
            page += 1
            if page > max_pages:
                raise DicValidationError("employee pagination exceeded the configured page limit")
        if expected_total is None or len(items) != expected_total:
            raise DicValidationError("employee pagination did not match the reported total")
        return EmployeeListResult(
            items=tuple(items),
            page=1,
            page_size=max(1, len(items)),
            total=expected_total,
            has_next=False,
        )

    async def get_employee_summary(self, employee_id: str) -> EmployeeSummary:
        return await self.adapter.get_employee_summary(employee_id)

    async def get_contracts(self, employee_id: str) -> tuple[ContractRecord, ...]:
        return await self.adapter.get_contracts(employee_id)

    async def get_roles(self, employee_id: str) -> RolesResult:
        return await self.adapter.get_roles(employee_id)

    async def get_time_access(self, employee_id: str) -> TimeAccessResult:
        return await self.adapter.get_time_access(employee_id)

    async def get_maturations(self, employee_id: str) -> tuple[MaturationRecord, ...]:
        return await self.adapter.get_maturations(employee_id)

    async def get_balance(self, employee_id: str, year: int) -> BalanceResult:
        return await self.adapter.get_balance(employee_id, year)

    async def get_balance_correction_state(
        self, employee_id: str, year: int, month: int, category: str
    ) -> BalanceCorrectionState:
        return await self.adapter.get_balance_correction_state(employee_id, year, month, category)

    async def get_payroll_metadata(
        self, employee_id: str, year: int | None = None
    ) -> tuple[PayrollMetadata, ...]:
        return await self.adapter.get_payroll_metadata(employee_id, year)

    async def list_notifications(self) -> NotificationListResult:
        return await self.adapter.list_notifications()

    async def find_employees_with_payroll(
        self,
        *,
        year: int,
        month: int,
    ) -> PayrollPresenceResult:
        """Compare the declared payroll view for every employee without parallel browser use.

        The adapter owns one Playwright page, so this traverses registered employee-list and
        payroll routes serially. It never accepts a model-provided URL or selector and fails
        closed when the bounded listing or a payroll row is inconsistent.
        """

        if not 2000 <= year <= 2200:
            raise DicValidationError("payroll year must be between 2000 and 2200")
        if not 1 <= month <= 12:
            raise DicValidationError("payroll month must be between 1 and 12")
        employees = await self.list_all_employees(
            EmployeeListQuery(employee_filter=EmployeeFilter.ALL),
            max_records=_PAYROLL_PRESENCE_MAX_EMPLOYEES,
        )
        matches: list[EmployeeListItem] = []
        for employee in employees.items:
            records = await self.adapter.get_payroll_metadata(employee.employee_id, year)
            for record in records:
                if record.employee_id != employee.employee_id or record.year != year:
                    raise DicValidationError(
                        "payroll metadata does not match the requested employee"
                    )
            if any(record.month == month for record in records):
                matches.append(employee)
        return PayrollPresenceResult(
            year=year,
            month=month,
            scanned=employees.total,
            employees=tuple(matches),
        )

    async def get_document_metadata(
        self, employee_id: str, query: DocumentQuery
    ) -> tuple[DocumentMetadata, ...]:
        return await self.adapter.get_document_metadata(employee_id, query)

    async def close(self) -> None:
        await self.adapter.close()
