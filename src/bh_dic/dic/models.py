"""Strict, redaction-aware models shared by every DIC adapter."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    field_validator,
    model_validator,
)

EmployeeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
RecordId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")]
OpaqueStateDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=512)]
_PARAMETER_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _validate_parameters(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if len(value) > 32:
        raise ValueError("too many action parameters")

    def validate(item: JsonValue, depth: int) -> None:
        if depth > 4:
            raise ValueError("action parameters are too deeply nested")
        if isinstance(item, str):
            if len(item) > 4_096:
                raise ValueError("action parameter string is too long")
            return
        if isinstance(item, float) and not isfinite(item):
            raise ValueError("action parameter number must be finite")
        if isinstance(item, list):
            if len(item) > 50:
                raise ValueError("action parameter list is too long")
            for child in item:
                validate(child, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 32:
                raise ValueError("action parameter object is too large")
            for key, child in item.items():
                if _PARAMETER_KEY.fullmatch(key) is None:
                    raise ValueError("invalid nested action parameter key")
                validate(child, depth + 1)

    for key, item in value.items():
        if _PARAMETER_KEY.fullmatch(key) is None:
            raise ValueError("invalid action parameter key")
        validate(item, 0)
    return value


class StrictModel(BaseModel):
    """Immutable Pydantic base used at all trust boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class FunctionId(StrEnum):
    EMP_READ_001 = "EMP-READ-001"
    EMP_READ_002 = "EMP-READ-002"
    EMP_SEARCH_001 = "EMP-SEARCH-001"
    EMP_FILTER_001 = "EMP-FILTER-001"
    EMP_SORT_001 = "EMP-SORT-001"
    EMP_PAGE_001 = "EMP-PAGE-001"
    EMP_CONTRACT_001 = "EMP-CONTRACT-001"
    EMP_RBAC_001 = "EMP-RBAC-001"
    EMP_TIME_001 = "EMP-TIME-001"
    EMP_MAT_001 = "EMP-MAT-001"
    EMP_BAL_001 = "EMP-BAL-001"
    EMP_PAY_001 = "EMP-PAY-001"
    EMP_DOC_001 = "EMP-DOC-001"
    EMP_UPDATE_001 = "EMP-UPDATE-001"
    EMP_CREATE_001 = "EMP-CREATE-001"
    EMP_CONTRACT_002 = "EMP-CONTRACT-002"
    EMP_CONTRACT_003 = "EMP-CONTRACT-003"
    EMP_MAT_002 = "EMP-MAT-002"
    EMP_BAL_002 = "EMP-BAL-002"
    EMP_CONNECT_001 = "EMP-CONNECT-001"
    EMP_CONNECT_002 = "EMP-CONNECT-002"
    EMP_INVITE_001 = "EMP-INVITE-001"
    EMP_INVITE_002 = "EMP-INVITE-002"
    EMP_RBAC_002 = "EMP-RBAC-002"
    EMP_STATUS_001 = "EMP-STATUS-001"
    EMP_STATUS_002 = "EMP-STATUS-002"
    EMP_DOC_002 = "EMP-DOC-002"
    EMP_DOC_003 = "EMP-DOC-003"
    EMP_DOC_004 = "EMP-DOC-004"
    EMP_DOC_005 = "EMP-DOC-005"
    EMP_DELETE_001 = "EMP-DELETE-001"
    EMP_EXPORT_001 = "EMP-EXPORT-001"


class EmployeeFilter(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class AccountState(StrEnum):
    CONNECTED = "connected"
    INVITED = "invited"
    NOT_CONNECTED = "not_connected"
    UNKNOWN = "unknown"


class EmployeeState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class OperationStatus(StrEnum):
    PREPARED = "prepared"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ReconciliationState(StrEnum):
    CONFIRMED_APPLIED = "confirmed_applied"
    CONFIRMED_NOT_APPLIED = "confirmed_not_applied"
    UNKNOWN = "unknown"


class SessionState(StrEnum):
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    MISSING = "missing"
    UNKNOWN = "unknown"


class DicCredentials(StrictModel):
    username: Annotated[str, Field(min_length=1, max_length=320)]
    password: SecretStr
    totp: SecretStr | None = None


class SessionStatus(StrictModel):
    state: SessionState
    authenticated_at: datetime | None = None
    expires_at: datetime | None = None
    account_hint_redacted: str | None = Field(default=None, max_length=64)


class HealthStatus(StrictModel):
    ready: bool
    authenticated: bool
    browser_available: bool
    detail: str = Field(max_length=256)


class EmployeeListQuery(StrictModel):
    query: str | None = Field(default=None, max_length=128)
    employee_filter: EmployeeFilter = EmployeeFilter.ACTIVE
    sort_by: Literal["name", "payroll_number", "status", "contract"] = "name"
    sort_direction: SortDirection = SortDirection.ASC
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=25, ge=1, le=100)


class EmployeeListItem(StrictModel):
    employee_id: EmployeeId
    display_name_redacted: str = Field(max_length=128)
    email_redacted: str | None = Field(default=None, max_length=320)
    tax_code_redacted: str | None = Field(default=None, max_length=32)
    job_title: str | None = Field(default=None, max_length=128)
    group_name: str | None = Field(default=None, max_length=128)
    payroll_number: str | None = Field(default=None, max_length=64)
    contract_label: str | None = Field(default=None, max_length=128)
    contract_state: str | None = Field(default=None, max_length=64)
    contract_period: str | None = Field(default=None, max_length=128)
    schedule_model: str | None = Field(default=None, max_length=128)
    workplace: str | None = Field(default=None, max_length=128)
    account_state: AccountState = AccountState.UNKNOWN
    employee_state: EmployeeState = EmployeeState.UNKNOWN


class EmployeeListResult(StrictModel):
    items: tuple[EmployeeListItem, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    has_next: bool


class EmployeeSummary(StrictModel):
    employee_id: EmployeeId
    first_name_redacted: str | None = Field(default=None, max_length=128)
    last_name_redacted: str | None = Field(default=None, max_length=128)
    payroll_number: str | None = Field(default=None, max_length=64)
    tax_code_redacted: str | None = Field(default=None, max_length=32)
    birth_date_redacted: str | None = Field(default=None, max_length=32)
    iban_redacted: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=128)
    phone_redacted: str | None = Field(default=None, max_length=64)
    business_email_redacted: str | None = Field(default=None, max_length=320)
    address_redacted: str | None = Field(default=None, max_length=256)
    workplace: str | None = Field(default=None, max_length=128)
    notes_redacted: str | None = Field(default=None, max_length=256)
    state: EmployeeState = EmployeeState.UNKNOWN


class RoleAssignment(StrictModel):
    name: NonEmptyText
    enabled: bool


class RolesResult(StrictModel):
    employee_id: EmployeeId
    groups: tuple[str, ...] = ()
    roles: tuple[RoleAssignment, ...] = ()


class TimeAccessResult(StrictModel):
    employee_id: EmployeeId
    timestamping_enabled: bool | None = None
    attendance_sheet_access: bool | None = None
    shift_management: bool | None = None
    expense_access: bool | None = None


class ContractRecord(StrictModel):
    contract_id: RecordId
    employee_id: EmployeeId
    stable_identifier: bool = False
    actionable: bool = False
    schedule: str | None = Field(default=None, max_length=128)
    flexibility: str | None = Field(default=None, max_length=128)
    permanent: bool | None = None
    start_date: str | None = Field(default=None, max_length=32)
    end_date: str | None = Field(default=None, max_length=32)
    ccnl_level: str | None = Field(default=None, max_length=128)
    work_regime: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=256)
    contract_type: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)
    period: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_actionability(self) -> ContractRecord:
        if self.actionable and not self.stable_identifier:
            raise ValueError("an actionable contract requires a stable DOM identifier")
        return self


class MaturationRecord(StrictModel):
    maturation_id: RecordId
    employee_id: EmployeeId
    category: str = Field(max_length=128)
    valid_from: str | None = Field(default=None, max_length=32)
    valid_to: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=64)


class BalanceLine(StrictModel):
    category: str = Field(max_length=128)
    previous_year: str | None = Field(default=None, max_length=64)
    previous_month: str | None = Field(default=None, max_length=64)
    accrued: str | None = Field(default=None, max_length=64)
    used: str | None = Field(default=None, max_length=64)
    corrections: str | None = Field(default=None, max_length=64)
    current_residual: str | None = Field(default=None, max_length=64)


class BalanceResult(StrictModel):
    employee_id: EmployeeId
    year: int = Field(ge=2000, le=2200)
    lines: tuple[BalanceLine, ...]


class BalanceCorrectionState(StrictModel):
    employee_id: EmployeeId
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    category: str = Field(min_length=1, max_length=128)
    current_value: str = Field(min_length=1, max_length=64)


class PayrollMetadata(StrictModel):
    payroll_id: RecordId
    employee_id: EmployeeId
    year: int = Field(ge=2000, le=2200)
    month: int | None = Field(default=None, ge=1, le=12)
    status: str | None = Field(default=None, max_length=64)
    published_at: str | None = Field(default=None, max_length=64)


class DocumentMetadata(StrictModel):
    document_id: RecordId
    employee_id: EmployeeId
    stable_identifier: bool = False
    actionable: bool = False
    title_redacted: str = Field(max_length=256)
    category: str | None = Field(default=None, max_length=128)
    expiry_date: str | None = Field(default=None, max_length=32)
    uploaded_at: str | None = Field(default=None, max_length=64)
    uploaded_by_redacted: str | None = Field(default=None, max_length=128)
    state: Literal["uploaded", "pending", "unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_actionability(self) -> DocumentMetadata:
        if self.actionable and not self.stable_identifier:
            raise ValueError("an actionable document requires a stable DOM identifier")
        return self


class DocumentQuery(StrictModel):
    query: str | None = Field(default=None, max_length=128)
    state: Literal["uploaded", "pending", "all"] = "all"
    category: str | None = Field(default=None, max_length=128)


class WriteRequest(StrictModel):
    function_id: FunctionId
    employee_id: EmployeeId | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    correlation_id: Annotated[str, Field(min_length=8, max_length=128)]

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_parameters(value)


class PreviewChange(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    before_redacted: str | None = Field(default=None, max_length=512)
    after_redacted: str | None = Field(default=None, max_length=512)


class PreparedAction(StrictModel):
    action_id: Annotated[
        str,
        Field(
            pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        ),
    ]
    function_id: FunctionId
    employee_id: EmployeeId | None = None
    parameters: dict[str, JsonValue] = Field(repr=False)
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    correlation_id: Annotated[str, Field(min_length=8, max_length=128)]
    request_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    preview: tuple[PreviewChange, ...]
    required_approvals: int = Field(ge=0, le=2)
    created_at: datetime
    expires_at: datetime
    status: Literal[OperationStatus.PREPARED] = OperationStatus.PREPARED

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_parameters(value)

    @model_validator(mode="after")
    def validate_lifetime(self) -> PreparedAction:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class ApprovalEvidence(StrictModel):
    action_id: str = Field(min_length=36, max_length=36)
    approver_id: str = Field(min_length=1, max_length=128)
    approved_at: datetime


class ExecutionResult(StrictModel):
    action_id: str = Field(min_length=36, max_length=36)
    function_id: FunctionId
    status: OperationStatus
    changed: bool
    postcondition_verified: bool
    message: str = Field(max_length=512)
    correlation_id: str = Field(min_length=1, max_length=128)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ReconciliationResult(StrictModel):
    action_id: str = Field(min_length=36, max_length=36)
    state: ReconciliationState
    detail: str = Field(max_length=512)


class StoredSessionStorageEntry(StrictModel):
    """One bounded browser sessionStorage entry."""

    model_config = ConfigDict(str_strip_whitespace=False)

    key: str = Field(max_length=256)
    value: str = Field(max_length=32_768, repr=False)


class StoredDicSessionStorage(StrictModel):
    """Closed sessionStorage snapshot for the one trusted DIC origin."""

    model_config = ConfigDict(str_strip_whitespace=False)

    origin: Literal["https://secure.dipendentincloud.it"]
    entries: tuple[StoredSessionStorageEntry, ...] = Field(max_length=64, repr=False)

    @field_validator("entries", mode="before")
    @classmethod
    def normalize_json_entries(cls, value: object) -> object:
        # JSON arrays are the canonical wire representation. Convert them before strict tuple
        # validation so encrypted vaults can round-trip through model_validate_json().
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_bounded_unique_entries(self) -> StoredDicSessionStorage:
        seen: set[str] = set()
        total_bytes = 0
        for entry in self.entries:
            key_bytes = len(entry.key.encode("utf-8"))
            value_bytes = len(entry.value.encode("utf-8"))
            if key_bytes > 256:
                raise ValueError("DIC sessionStorage key is too large")
            if value_bytes > 32_768:
                raise ValueError("DIC sessionStorage value is too large")
            if entry.key in seen:
                raise ValueError("DIC sessionStorage contains duplicate keys")
            seen.add(entry.key)
            total_bytes += key_bytes + value_bytes
        if total_bytes > 131_072:
            raise ValueError("DIC sessionStorage snapshot is too large")
        return self


class StoredBrowserSession(StrictModel):
    storage_state: dict[str, JsonValue] = Field(repr=False)
    session_storage: StoredDicSessionStorage | None = Field(default=None, repr=False)
    authenticated_at: datetime
    expires_at: datetime
    account_hint_redacted: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_lifetime(self) -> StoredBrowserSession:
        if self.expires_at <= self.authenticated_at:
            raise ValueError("expires_at must be after authenticated_at")
        return self
