"""Deterministic, synthetic adapter used by tests and safe mock mode."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from bh_dic.dic.catalog import FORBIDDEN_FUNCTIONS, MUTATING_FUNCTIONS
from bh_dic.dic.errors import DicConfigurationError, DicNotFoundError, DicValidationError
from bh_dic.dic.models import (
    AccountState,
    BalanceCorrectionState,
    BalanceLine,
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
    ExecutionResult,
    FunctionId,
    HealthStatus,
    MaturationRecord,
    NotificationListResult,
    NotificationRecord,
    OpaqueStateDigest,
    OperationStatus,
    PayrollMetadata,
    PreparedAction,
    ReconciliationResult,
    ReconciliationState,
    RoleAssignment,
    RolesResult,
    SessionState,
    SessionStatus,
    SortDirection,
    TimeAccessResult,
)
from bh_dic.dic.values import canonical_decimal_text

_MOCK_SUMMARY_STATE_FUNCTIONS = frozenset(
    {
        FunctionId.EMP_UPDATE_001,
        FunctionId.EMP_CONNECT_001,
        FunctionId.EMP_CONNECT_002,
        FunctionId.EMP_INVITE_001,
        FunctionId.EMP_INVITE_002,
        FunctionId.EMP_STATUS_001,
        FunctionId.EMP_STATUS_002,
        FunctionId.EMP_DELETE_001,
    }
)
_EMPLOYEE_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "payroll_number",
        "tax_code",
        "birth_date",
        "iban",
        "job_title",
        "phone",
        "business_email",
        "address",
        "workplace",
        "notes",
    }
)
_CONTRACT_FIELDS = frozenset(
    {
        "schedule",
        "flexibility",
        "permanent",
        "start_date",
        "end_date",
        "ccnl_level",
        "work_regime",
        "description",
        "contract_type",
    }
)
_UPLOAD_INTERNAL_FIELDS = frozenset(
    {"safe_local_path", "safe_local_sha256", "safe_local_size", "detected_mime"}
)


class MockDicAdapter:
    """In-memory adapter containing no production identifiers or personal data."""

    _SYNTHETIC_DIGEST_KEY = hashlib.sha256(b"BH-DiC mock state digest key").digest()

    def __init__(self, *, state_digest_key: bytes | None = None) -> None:
        if state_digest_key is not None and len(state_digest_key) < 32:
            raise DicConfigurationError("DIC state digest key must contain at least 32 bytes")
        self._state_digest_key = (
            bytes(state_digest_key) if state_digest_key is not None else self._SYNTHETIC_DIGEST_KEY
        )
        self._closed = False
        self._authenticated = True
        self._items: dict[str, EmployeeListItem] = {}
        self._summaries: dict[str, EmployeeSummary] = {}
        self._raw_summaries: dict[str, dict[str, str]] = {}
        self._contracts: dict[str, list[ContractRecord]] = {}
        self._roles: dict[str, RolesResult] = {}
        self._time_access: dict[str, TimeAccessResult] = {}
        self._maturations: dict[str, list[MaturationRecord]] = {}
        self._balances: dict[tuple[str, int], BalanceResult] = {}
        self._balance_corrections: dict[tuple[str, int, int, str], str] = {}
        self._payrolls: dict[str, list[PayrollMetadata]] = {}
        self._documents: dict[str, list[DocumentMetadata]] = {}
        self._notifications: dict[int, NotificationRecord] = {}
        self._executions: dict[str, ExecutionResult] = {}
        self._effect_targets: dict[str, str] = {}
        self._effect_events: set[str] = set()
        self._seed()

    def _seed(self) -> None:
        employee_id = "EMP-SYNTH-001"
        self._items[employee_id] = EmployeeListItem(
            employee_id=employee_id,
            display_name_redacted="A. E.",
            email_redacted="a***@example.invalid",
            tax_code_redacted="************000X",
            job_title="Synthetic tester",
            group_name="Quality",
            payroll_number="SYN-001",
            contract_label="Contratto indeterminato",
            contract_state="active",
            contract_period="dal 2024-01-01",
            schedule_model="Full time synthetic",
            workplace="Synthetic office",
            account_state=AccountState.CONNECTED,
            employee_state=EmployeeState.ACTIVE,
        )
        self._summaries[employee_id] = EmployeeSummary(
            employee_id=employee_id,
            first_name_redacted="A.",
            last_name_redacted="E.",
            payroll_number="SYN-001",
            tax_code_redacted="************000X",
            birth_date_redacted="****-**-01",
            iban_redacted="IT** **** **** **** **** ***",
            job_title="Synthetic tester",
            phone_redacted="+39 *** *** 0000",
            business_email_redacted="a***@example.invalid",
            address_redacted="[REDACTED]",
            workplace="Synthetic office",
            notes_redacted="[REDACTED]",
            state=EmployeeState.ACTIVE,
        )
        self._raw_summaries[employee_id] = {
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
        self._contracts[employee_id] = [
            ContractRecord(
                contract_id="CON-SYNTH-001",
                employee_id=employee_id,
                stable_identifier=True,
                actionable=True,
                schedule="40h",
                flexibility="none",
                permanent=True,
                start_date="2024-01-01",
                ccnl_level="Synthetic level",
                work_regime="full-time",
                description="Synthetic contract",
                contract_type="indeterminato",
                status="active",
                period="2024-present",
            )
        ]
        self._roles[employee_id] = RolesResult(
            employee_id=employee_id,
            groups=("Quality",),
            roles=(RoleAssignment(name="Employee", enabled=True),),
        )
        self._time_access[employee_id] = TimeAccessResult(
            employee_id=employee_id,
            timestamping_enabled=True,
            attendance_sheet_access=True,
            shift_management=False,
            expense_access=False,
        )
        self._maturations[employee_id] = [
            MaturationRecord(
                maturation_id="MAT-SYNTH-001",
                employee_id=employee_id,
                category="Ferie",
                valid_from="2026-01-01",
                valid_to="2026-12-31",
                status="valid",
            )
        ]
        self._balances[(employee_id, 2026)] = BalanceResult(
            employee_id=employee_id,
            year=2026,
            lines=(
                BalanceLine(
                    category="Ferie",
                    previous_year="0",
                    previous_month="8",
                    accrued="4",
                    used="2",
                    corrections="0",
                    current_residual="10",
                ),
            ),
        )
        self._balance_corrections[(employee_id, 2026, 8, "ferie")] = "0"
        self._payrolls[employee_id] = [
            PayrollMetadata(
                payroll_id="PAY-SYNTH-001",
                employee_id=employee_id,
                year=2026,
                month=1,
                status="published",
                published_at="2026-02-01",
            )
        ]
        self._documents[employee_id] = [
            DocumentMetadata(
                document_id="DOC-SYNTH-001",
                employee_id=employee_id,
                stable_identifier=True,
                actionable=True,
                title_redacted="Documento sintetico [REDACTED]",
                category="CV",
                expiry_date=None,
                uploaded_at="2026-01-15",
                uploaded_by_redacted="A. A.",
                state="uploaded",
            )
        ]
        self._notifications[1] = NotificationRecord(
            notification_id=1,
            notification_type="synthetic",
            text="Notifica DIC sintetica",
            created_at="2026-08-20T10:00:00+00:00",
            read=False,
        )

    def _require_open(self) -> None:
        if self._closed:
            raise DicValidationError("mock adapter is closed")

    def _require_employee(self, employee_id: str) -> None:
        if employee_id not in self._items:
            raise DicNotFoundError("employee not found")

    async def health(self) -> HealthStatus:
        return HealthStatus(
            ready=not self._closed,
            authenticated=self._authenticated,
            browser_available=False,
            detail="synthetic mock adapter",
        )

    async def ensure_authenticated(
        self, credentials: DicCredentials | None = None
    ) -> SessionStatus:
        del credentials
        self._require_open()
        self._authenticated = True
        return await self.session_status()

    async def session_status(self) -> SessionStatus:
        return SessionStatus(
            state=SessionState.AUTHENTICATED if self._authenticated else SessionState.MISSING,
            account_hint_redacted="mock@example.invalid",
        )

    async def list_employees(self, query: EmployeeListQuery) -> EmployeeListResult:
        self._require_open()
        items = list(self._items.values())
        if query.employee_filter is EmployeeFilter.ACTIVE:
            items = [item for item in items if item.employee_state is EmployeeState.ACTIVE]
        elif query.employee_filter is EmployeeFilter.INACTIVE:
            items = [item for item in items if item.employee_state is EmployeeState.INACTIVE]
        if query.query:
            needle = query.query.casefold()
            items = [
                item
                for item in items
                if needle in item.employee_id.casefold()
                or needle in item.display_name_redacted.casefold()
                or (item.payroll_number is not None and needle in item.payroll_number.casefold())
            ]
        key_functions = {
            "name": lambda item: item.display_name_redacted.casefold(),
            "payroll_number": lambda item: (item.payroll_number or "").casefold(),
            "status": lambda item: item.employee_state.value,
            "contract": lambda item: (item.contract_label or "").casefold(),
        }
        items.sort(
            key=key_functions[query.sort_by],
            reverse=query.sort_direction is SortDirection.DESC,
        )
        total = len(items)
        start = (query.page - 1) * query.page_size
        selected = tuple(items[start : start + query.page_size])
        return EmployeeListResult(
            items=selected,
            page=query.page,
            page_size=query.page_size,
            total=total,
            has_next=start + query.page_size < total,
        )

    async def get_employee_summary(self, employee_id: str) -> EmployeeSummary:
        self._require_employee(employee_id)
        return self._summaries[employee_id]

    async def get_contracts(self, employee_id: str) -> tuple[ContractRecord, ...]:
        self._require_employee(employee_id)
        return tuple(self._contracts.get(employee_id, ()))

    async def get_roles(self, employee_id: str) -> RolesResult:
        self._require_employee(employee_id)
        return self._roles.get(employee_id, RolesResult(employee_id=employee_id))

    async def get_time_access(self, employee_id: str) -> TimeAccessResult:
        self._require_employee(employee_id)
        return self._time_access.get(employee_id, TimeAccessResult(employee_id=employee_id))

    async def get_maturations(self, employee_id: str) -> tuple[MaturationRecord, ...]:
        self._require_employee(employee_id)
        return tuple(self._maturations.get(employee_id, ()))

    async def get_balance(self, employee_id: str, year: int) -> BalanceResult:
        self._require_employee(employee_id)
        return self._balances.get(
            (employee_id, year), BalanceResult(employee_id=employee_id, year=year, lines=())
        )

    async def get_balance_correction_state(
        self, employee_id: str, year: int, month: int, category: str
    ) -> BalanceCorrectionState:
        self._require_employee(employee_id)
        if isinstance(month, bool) or month not in range(1, 13):
            raise DicValidationError("month must be between 1 and 12")
        normalized_category = category.strip().casefold()
        matches = [
            line
            for line in (await self.get_balance(employee_id, year)).lines
            if line.category.strip().casefold() == normalized_category
        ]
        if len(matches) != 1:
            raise DicValidationError("balance category must identify exactly one row")
        key = (employee_id, year, month, normalized_category)
        try:
            current = self._balance_corrections[key]
        except KeyError as exc:
            raise DicValidationError("balance month/category correction is unavailable") from exc
        return BalanceCorrectionState(
            employee_id=employee_id,
            year=year,
            month=month,
            category=matches[0].category,
            current_value=canonical_decimal_text(current),
        )

    async def get_payroll_metadata(
        self, employee_id: str, year: int | None = None
    ) -> tuple[PayrollMetadata, ...]:
        self._require_employee(employee_id)
        records = self._payrolls.get(employee_id, ())
        return tuple(record for record in records if year is None or record.year == year)

    async def list_notifications(self) -> NotificationListResult:
        self._require_open()
        items = tuple(
            sorted(
                self._notifications.values(),
                key=lambda item: (item.created_at, item.notification_id),
                reverse=True,
            )
        )
        return NotificationListResult(items=items, total=len(items))

    async def get_document_metadata(
        self, employee_id: str, query: DocumentQuery
    ) -> tuple[DocumentMetadata, ...]:
        self._require_employee(employee_id)
        documents = self._documents.get(employee_id, ())
        result = []
        for document in documents:
            if query.state != "all" and document.state != query.state:
                continue
            if query.category and document.category != query.category:
                continue
            if query.query and query.query.casefold() not in document.title_redacted.casefold():
                continue
            result.append(document)
        return tuple(result)

    async def get_state_digest(
        self,
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
    ) -> OpaqueStateDigest:
        """Key a canonical synthetic resource snapshot without exposing its material."""

        execution_only = {
            "safe_local_path",
            "safe_local_sha256",
            "safe_local_size",
            "detected_mime",
        }
        clean_parameters = {
            key: value for key, value in parameters.items() if key not in execution_only
        }
        material: object

        if function_id is FunctionId.EMP_NOTIF_002:
            notification_id = clean_parameters.get("notification_id")
            read = clean_parameters.get("read")
            if (
                not isinstance(notification_id, int)
                or isinstance(notification_id, bool)
                or not isinstance(read, bool)
                or notification_id not in self._notifications
            ):
                raise DicValidationError("notification state parameters are invalid")
            material = self._notifications[notification_id].model_dump(mode="json")
        elif function_id in {FunctionId.EMP_CREATE_001, FunctionId.EMP_EXPORT_001}:
            material = [self._items[key].model_dump(mode="json") for key in sorted(self._items)]
        else:
            if employee_id is None:
                raise DicValidationError("mutation state digest requires employee_id")
            if function_id is FunctionId.EMP_DELETE_001 and employee_id not in self._items:
                material = {"employee_id": employee_id, "state": "missing"}
            elif function_id in _MOCK_SUMMARY_STATE_FUNCTIONS:
                self._require_employee(employee_id)
                if function_id is FunctionId.EMP_UPDATE_001:
                    if not clean_parameters or set(clean_parameters).difference(_EMPLOYEE_FIELDS):
                        raise DicValidationError("employee update state cannot be verified")
                    if all(
                        self._matches(self._raw_summaries[employee_id].get(key), value)
                        for key, value in clean_parameters.items()
                    ):
                        raise DicValidationError("employee update would not change state")
                material = {
                    "item": self._items[employee_id].model_dump(mode="json"),
                    "raw_summary": self._raw_summaries[employee_id],
                    "summary": self._summaries[employee_id].model_dump(mode="json"),
                }
            elif function_id in {FunctionId.EMP_CONTRACT_002, FunctionId.EMP_CONTRACT_003}:
                self._require_employee(employee_id)
                contract_records = self._contracts.get(employee_id, ())
                contract_id = clean_parameters.get("contract_id")
                if isinstance(contract_id, str):
                    contract_matches = [
                        record for record in contract_records if record.contract_id == contract_id
                    ]
                    if (
                        len(contract_matches) != 1
                        or not contract_matches[0].stable_identifier
                        or not contract_matches[0].actionable
                    ):
                        raise DicValidationError("contract target is not stable and actionable")
                    if function_id is FunctionId.EMP_CONTRACT_002:
                        expected = {
                            key: value
                            for key, value in clean_parameters.items()
                            if key in _CONTRACT_FIELDS
                        }
                        if not expected:
                            raise DicValidationError("contract update state cannot be verified")
                        if all(
                            self._matches(getattr(contract_matches[0], key), value)
                            for key, value in expected.items()
                        ):
                            raise DicValidationError("contract update would not change state")
                material = [record.model_dump(mode="json") for record in contract_records]
            elif function_id is FunctionId.EMP_MAT_002:
                self._require_employee(employee_id)
                material = [
                    record.model_dump(mode="json")
                    for record in self._maturations.get(employee_id, ())
                ]
            elif function_id is FunctionId.EMP_BAL_002:
                self._require_employee(employee_id)
                material = {
                    "balances": [
                        value.model_dump(mode="json")
                        for key, value in sorted(self._balances.items())
                        if key[0] == employee_id
                    ],
                    "corrections": [
                        [list(key[1:]), value]
                        for key, value in sorted(self._balance_corrections.items())
                        if key[0] == employee_id
                    ],
                }
            elif function_id is FunctionId.EMP_RBAC_002:
                self._require_employee(employee_id)
                material = {
                    "roles": self._roles[employee_id].model_dump(mode="json"),
                    "time_access": self._time_access[employee_id].model_dump(mode="json"),
                }
            elif function_id in {
                FunctionId.EMP_DOC_002,
                FunctionId.EMP_DOC_003,
                FunctionId.EMP_DOC_004,
                FunctionId.EMP_DOC_005,
            }:
                self._require_employee(employee_id)
                document_records = self._documents.get(employee_id, ())
                document_id = clean_parameters.get("document_id")
                if isinstance(document_id, str):
                    document_matches = [
                        record for record in document_records if record.document_id == document_id
                    ]
                    if (
                        len(document_matches) != 1
                        or not document_matches[0].stable_identifier
                        or not document_matches[0].actionable
                    ):
                        raise DicValidationError("document target is not stable and actionable")
                material = [record.model_dump(mode="json") for record in document_records]
            else:
                raise DicValidationError("function has no mutation state digest plan")

        canonical = json.dumps(
            {
                "employee_id": employee_id,
                "function_id": function_id.value,
                "parameters": clean_parameters,
                "schema": "bh-dic-mock-state-v1",
                "state": material,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._state_digest_key, canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _string_parameter(action: PreparedAction, key: str, *, required: bool = True) -> str | None:
        value = action.parameters.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise DicValidationError(f"parameter {key!r} must be a non-empty string")
        return value.strip()

    def _set_employee_state(self, employee_id: str, state: EmployeeState) -> None:
        item = self._items[employee_id]
        self._items[employee_id] = EmployeeListItem.model_validate(
            {**item.model_dump(), "employee_state": state}
        )
        summary = self._summaries[employee_id]
        self._summaries[employee_id] = EmployeeSummary.model_validate(
            {**summary.model_dump(), "state": state}
        )

    def _set_account_state(self, employee_id: str, state: AccountState) -> None:
        item = self._items[employee_id]
        self._items[employee_id] = EmployeeListItem.model_validate(
            {**item.model_dump(), "account_state": state}
        )

    def _execute_employee_write(self, action: PreparedAction, employee_id: str) -> None:
        parameterless = {
            FunctionId.EMP_CONNECT_001,
            FunctionId.EMP_CONNECT_002,
            FunctionId.EMP_INVITE_001,
            FunctionId.EMP_INVITE_002,
            FunctionId.EMP_STATUS_001,
            FunctionId.EMP_STATUS_002,
            FunctionId.EMP_DELETE_001,
        }
        if action.function_id in parameterless and action.parameters:
            raise DicValidationError("action does not accept parameters")
        if action.function_id is FunctionId.EMP_UPDATE_001:
            unknown = set(action.parameters).difference(_EMPLOYEE_FIELDS)
            if unknown:
                raise DicValidationError(f"unsupported employee fields: {sorted(unknown)}")
            if not action.parameters:
                raise DicValidationError("employee update requires at least one field")
            raw = self._raw_summaries[employee_id]
            if all(self._matches(raw.get(key), value) for key, value in action.parameters.items()):
                raise DicValidationError("employee update would not change state")
            for key, value in action.parameters.items():
                if not isinstance(value, str) or not value.strip():
                    raise DicValidationError(f"parameter {key!r} must be a non-empty string")
                raw[key] = value.strip()
            summary = self._summaries[employee_id]
            updates: dict[str, JsonValue] = {
                "first_name_redacted": f"{raw['first_name'][0].upper()}.",
                "last_name_redacted": f"{raw['last_name'][0].upper()}.",
                "payroll_number": raw["payroll_number"],
                "tax_code_redacted": f"************{raw['tax_code'][-4:]}",
                "birth_date_redacted": f"****-**-{raw['birth_date'][-2:]}",
                "iban_redacted": f"**********************{raw['iban'][-4:]}",
                "job_title": raw["job_title"],
                "phone_redacted": f"********{raw['phone'][-4:]}",
                "business_email_redacted": "***@example.invalid",
                "address_redacted": "[REDACTED]",
                "workplace": raw["workplace"],
                "notes_redacted": "[REDACTED]",
            }
            self._summaries[employee_id] = EmployeeSummary.model_validate(
                {**summary.model_dump(), **updates}
            )
            item = self._items[employee_id]
            self._items[employee_id] = EmployeeListItem.model_validate(
                {
                    **item.model_dump(),
                    "display_name_redacted": (
                        f"{raw['first_name'][0].upper()}. {raw['last_name'][0].upper()}."
                    ),
                    "email_redacted": "***@example.invalid",
                    "tax_code_redacted": f"************{raw['tax_code'][-4:]}",
                    "job_title": raw["job_title"],
                    "payroll_number": raw["payroll_number"],
                    "workplace": raw["workplace"],
                }
            )
        elif action.function_id is FunctionId.EMP_CONNECT_001:
            self._set_account_state(employee_id, AccountState.CONNECTED)
        elif action.function_id is FunctionId.EMP_CONNECT_002:
            self._set_account_state(employee_id, AccountState.NOT_CONNECTED)
        elif action.function_id is FunctionId.EMP_INVITE_001:
            self._set_account_state(employee_id, AccountState.INVITED)
            self._effect_events.add(action.idempotency_key)
        elif action.function_id is FunctionId.EMP_INVITE_002:
            self._set_account_state(employee_id, AccountState.NOT_CONNECTED)
        elif action.function_id is FunctionId.EMP_STATUS_001:
            self._set_employee_state(employee_id, EmployeeState.INACTIVE)
        elif action.function_id is FunctionId.EMP_STATUS_002:
            self._set_employee_state(employee_id, EmployeeState.ACTIVE)
        elif action.function_id is FunctionId.EMP_RBAC_002:
            if set(action.parameters) != {"role_name", "enabled"}:
                raise DicValidationError("role update requires only role_name and enabled")
            role_name = self._string_parameter(action, "role_name")
            enabled = action.parameters.get("enabled")
            if not isinstance(enabled, bool):
                raise DicValidationError("enabled must be boolean")
            current_roles = self._roles[employee_id]
            matches = [
                index
                for index, role in enumerate(current_roles.roles)
                if role.name.casefold() == (role_name or "").casefold()
            ]
            if len(matches) != 1:
                raise DicValidationError("role_name must identify exactly one role")
            roles = list(current_roles.roles)
            if roles[matches[0]].enabled is enabled:
                raise DicValidationError("role state already matches requested value")
            roles[matches[0]] = RoleAssignment(name=roles[matches[0]].name, enabled=enabled)
            self._roles[employee_id] = RolesResult(
                employee_id=employee_id,
                groups=current_roles.groups,
                roles=tuple(roles),
            )
        elif action.function_id is FunctionId.EMP_DELETE_001:
            for store in (
                self._items,
                self._summaries,
                self._raw_summaries,
                self._contracts,
                self._roles,
                self._time_access,
                self._maturations,
                self._payrolls,
                self._documents,
            ):
                store.pop(employee_id, None)
            for balance_key in [
                balance_key for balance_key in self._balances if balance_key[0] == employee_id
            ]:
                self._balances.pop(balance_key, None)
            for correction_key in [
                correction_key
                for correction_key in self._balance_corrections
                if correction_key[0] == employee_id
            ]:
                self._balance_corrections.pop(correction_key, None)

    def _execute_create(self, action: PreparedAction) -> str:
        allowed = _EMPLOYEE_FIELDS | {"creation_mode"}
        unknown = set(action.parameters).difference(allowed)
        if unknown:
            raise DicValidationError(f"unsupported create fields: {sorted(unknown)}")
        if action.parameters.get("creation_mode", "manual") != "manual":
            raise DicValidationError("only deterministic manual creation is implemented")
        first_name = self._string_parameter(action, "first_name")
        last_name = self._string_parameter(action, "last_name")
        employee_id = f"EMP-MOCK-{action.request_fingerprint[:8].upper()}"
        if employee_id in self._items:
            raise DicValidationError("employee already exists")
        raw = {
            key: self._string_parameter(action, key, required=False) or ""
            for key in _EMPLOYEE_FIELDS
        }
        raw["first_name"] = cast(str, first_name)
        raw["last_name"] = cast(str, last_name)
        self._raw_summaries[employee_id] = raw
        self._items[employee_id] = EmployeeListItem(
            employee_id=employee_id,
            display_name_redacted=f"{raw['first_name'][0].upper()}. {raw['last_name'][0].upper()}.",
            email_redacted="***@example.invalid" if raw["business_email"] else None,
            tax_code_redacted=(f"************{raw['tax_code'][-4:]}" if raw["tax_code"] else None),
            job_title=raw["job_title"] or None,
            payroll_number=raw["payroll_number"] or None,
            workplace=raw["workplace"] or None,
            employee_state=EmployeeState.ACTIVE,
            account_state=AccountState.NOT_CONNECTED,
        )
        self._summaries[employee_id] = EmployeeSummary(
            employee_id=employee_id,
            first_name_redacted=f"{raw['first_name'][0].upper()}.",
            last_name_redacted=f"{raw['last_name'][0].upper()}.",
            payroll_number=raw["payroll_number"] or None,
            tax_code_redacted=(f"************{raw['tax_code'][-4:]}" if raw["tax_code"] else None),
            birth_date_redacted=(
                f"****-**-{raw['birth_date'][-2:]}" if raw["birth_date"] else None
            ),
            iban_redacted=(f"**********************{raw['iban'][-4:]}" if raw["iban"] else None),
            job_title=raw["job_title"] or None,
            phone_redacted=(f"********{raw['phone'][-4:]}" if raw["phone"] else None),
            business_email_redacted=("***@example.invalid" if raw["business_email"] else None),
            address_redacted=("[REDACTED]" if raw["address"] else None),
            workplace=raw["workplace"] or None,
            notes_redacted=("[REDACTED]" if raw["notes"] else None),
            state=EmployeeState.ACTIVE,
        )
        return employee_id

    def _execute_related_write(self, action: PreparedAction, employee_id: str) -> None:
        suffix = action.request_fingerprint[:12].upper()
        if action.function_id is FunctionId.EMP_CONTRACT_002:
            unknown = set(action.parameters).difference(_CONTRACT_FIELDS | {"contract_id"})
            if unknown:
                raise DicValidationError(f"unsupported contract fields: {sorted(unknown)}")
            contract_id = self._string_parameter(action, "contract_id", required=False)
            contracts = self._contracts.setdefault(employee_id, [])
            existing: ContractRecord | None = None
            existing_index: int | None = None
            if contract_id is not None:
                matches = [
                    (index, record)
                    for index, record in enumerate(contracts)
                    if record.contract_id == contract_id
                ]
                if len(matches) != 1 or not matches[0][1].actionable:
                    raise DicNotFoundError("stable actionable contract not found")
                existing_index, existing = matches[0]
                contract_expected = {
                    key: value
                    for key, value in action.parameters.items()
                    if key in _CONTRACT_FIELDS
                }
                if contract_expected and all(
                    self._matches(getattr(existing, key), value)
                    for key, value in contract_expected.items()
                ):
                    raise DicValidationError("contract update would not change state")
            values: dict[str, object] = existing.model_dump() if existing is not None else {}
            for field in _CONTRACT_FIELDS:
                value = action.parameters.get(field)
                if value is None:
                    continue
                if field == "permanent":
                    if not isinstance(value, bool):
                        raise DicValidationError("permanent must be boolean")
                elif not isinstance(value, str) or not value.strip():
                    raise DicValidationError(f"parameter {field!r} must be a non-empty string")
                values[field] = value
            contract_record = ContractRecord.model_validate(
                {
                    **values,
                    "contract_id": contract_id or f"CON-{suffix}",
                    "employee_id": employee_id,
                    "stable_identifier": True,
                    "actionable": True,
                    "status": values.get("status", "active"),
                }
            )
            if existing_index is None:
                contracts.append(contract_record)
            else:
                contracts[existing_index] = contract_record
            self._effect_targets[action.idempotency_key] = contract_record.contract_id
        elif action.function_id is FunctionId.EMP_CONTRACT_003:
            if set(action.parameters) != {"contract_id"}:
                raise DicValidationError("contract delete requires only contract_id")
            contract_id = self._string_parameter(action, "contract_id")
            contracts = self._contracts.get(employee_id, [])
            remaining_contracts = [
                contract for contract in contracts if contract.contract_id != contract_id
            ]
            if len(remaining_contracts) == len(contracts):
                raise DicNotFoundError("contract not found")
            self._contracts[employee_id] = remaining_contracts
        elif action.function_id is FunctionId.EMP_MAT_002:
            if set(action.parameters).difference({"category", "valid_from", "valid_to"}):
                raise DicValidationError("maturation contains unsupported fields")
            category = self._string_parameter(action, "category")
            maturation_record = MaturationRecord(
                maturation_id=f"MAT-{suffix}",
                employee_id=employee_id,
                category=cast(str, category),
                valid_from=self._string_parameter(action, "valid_from", required=False),
                valid_to=self._string_parameter(action, "valid_to", required=False),
                status="valid",
            )
            self._maturations.setdefault(employee_id, []).append(maturation_record)
            self._effect_targets[action.idempotency_key] = maturation_record.maturation_id
        elif action.function_id is FunctionId.EMP_DOC_002:
            allowed = {"upload_id", "category", "expiry_date"} | _UPLOAD_INTERNAL_FIELDS
            if set(action.parameters).difference(allowed):
                raise DicValidationError("document upload contains unsupported metadata")
            category = self._string_parameter(action, "category")
            uploaded_document = DocumentMetadata(
                document_id=f"DOC-{suffix}",
                employee_id=employee_id,
                stable_identifier=True,
                actionable=True,
                title_redacted="Documento caricato [REDACTED]",
                category=category,
                expiry_date=self._string_parameter(action, "expiry_date", required=False),
                state="uploaded",
            )
            self._documents.setdefault(employee_id, []).append(uploaded_document)
            self._effect_targets[action.idempotency_key] = uploaded_document.document_id
        elif action.function_id is FunctionId.EMP_BAL_002:
            if set(action.parameters) != {
                "year",
                "month",
                "category",
                "previous_value",
                "amount",
            }:
                raise DicValidationError("balance correction parameter set is invalid")
            year = action.parameters.get("year")
            month = action.parameters.get("month")
            category = self._string_parameter(action, "category")
            previous_value = self._string_parameter(action, "previous_value")
            amount = self._string_parameter(action, "amount")
            if not isinstance(year, int) or isinstance(year, bool):
                raise DicValidationError("year must be an integer")
            if not isinstance(month, int) or isinstance(month, bool) or month not in range(1, 13):
                raise DicValidationError("month must be between 1 and 12")
            try:
                expected = canonical_decimal_text(previous_value)
                canonical_amount = canonical_decimal_text(amount)
            except ValueError as exc:
                raise DicValidationError("balance values must be canonical decimals") from exc
            state = self._balance_corrections.get(
                (employee_id, year, month, (category or "").casefold())
            )
            if state is None:
                raise DicValidationError("balance correction target is unavailable")
            if canonical_decimal_text(state) != expected:
                raise DicValidationError("balance correction precondition changed")
            balance = self._balances.get(
                (employee_id, year), BalanceResult(employee_id=employee_id, year=year, lines=())
            )
            lines = list(balance.lines)
            matching_indexes = [
                index
                for index, line in enumerate(lines)
                if line.category.casefold() == (category or "").casefold()
            ]
            if len(matching_indexes) != 1:
                raise DicValidationError("balance category must identify exactly one row")
            index = matching_indexes[0]
            lines[index] = BalanceLine.model_validate(
                {**lines[index].model_dump(), "corrections": canonical_amount}
            )
            self._balances[(employee_id, year)] = BalanceResult(
                employee_id=employee_id, year=year, lines=tuple(lines)
            )
            self._balance_corrections[(employee_id, year, month, (category or "").casefold())] = (
                canonical_amount
            )
        elif action.function_id is FunctionId.EMP_DOC_003:
            if set(action.parameters) != {"document_id"}:
                raise DicValidationError("document download requires only document_id")
            document_id = self._string_parameter(action, "document_id")
            download_matches = [
                document
                for document in self._documents.get(employee_id, ())
                if document.document_id == document_id and document.actionable
            ]
            if len(download_matches) != 1:
                raise DicNotFoundError("stable actionable document not found")
            self._effect_events.add(action.idempotency_key)
        elif action.function_id is FunctionId.EMP_DOC_004:
            if set(action.parameters).difference({"document_id", "category", "expiry_date"}):
                raise DicValidationError("document update contains unsupported metadata")
            if not ({"category", "expiry_date"} & set(action.parameters)):
                raise DicValidationError("document update requires category or expiry_date")
            document_id = self._string_parameter(action, "document_id")
            category = self._string_parameter(action, "category", required=False)
            expiry_date = self._string_parameter(action, "expiry_date", required=False)
            document_records = self._documents.get(employee_id, [])
            found = False
            for index, document_record in enumerate(document_records):
                if document_record.document_id == document_id:
                    document_records[index] = DocumentMetadata.model_validate(
                        {
                            **document_record.model_dump(),
                            "category": category or document_record.category,
                            "expiry_date": expiry_date or document_record.expiry_date,
                        }
                    )
                    found = True
                    break
            if not found:
                raise DicNotFoundError("document not found")
        elif action.function_id is FunctionId.EMP_DOC_005:
            if set(action.parameters) != {"document_id"}:
                raise DicValidationError("document delete requires only document_id")
            document_id = self._string_parameter(action, "document_id")
            document_records = self._documents.get(employee_id, [])
            remaining = [
                document_record
                for document_record in document_records
                if document_record.document_id != document_id
            ]
            if len(remaining) == len(document_records):
                raise DicNotFoundError("document not found")
            self._documents[employee_id] = remaining

    @staticmethod
    def _matches(actual: object, expected: object) -> bool:
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.strip().casefold() == expected.strip().casefold()
        return actual == expected

    def _postcondition_applied(self, action: PreparedAction) -> bool:
        employee_id = action.employee_id
        if action.function_id is FunctionId.EMP_NOTIF_002:
            notification_id = action.parameters.get("notification_id")
            read = action.parameters.get("read")
            return (
                isinstance(notification_id, int)
                and not isinstance(notification_id, bool)
                and isinstance(read, bool)
                and notification_id in self._notifications
                and self._notifications[notification_id].read is read
            )
        if action.function_id is FunctionId.EMP_CREATE_001:
            target = self._effect_targets.get(action.idempotency_key)
            if target is None or target not in self._raw_summaries:
                return False
            raw = self._raw_summaries[target]
            return all(
                key == "creation_mode" or self._matches(raw.get(key), value)
                for key, value in action.parameters.items()
            )
        if action.function_id is FunctionId.EMP_EXPORT_001:
            return action.idempotency_key in self._effect_events
        if employee_id is None:
            return False
        if action.function_id is FunctionId.EMP_DELETE_001:
            return employee_id not in self._items
        if employee_id not in self._items:
            return False
        if action.function_id is FunctionId.EMP_UPDATE_001:
            raw = self._raw_summaries[employee_id]
            return all(
                self._matches(raw.get(key), value) for key, value in action.parameters.items()
            )
        account_states = {
            FunctionId.EMP_CONNECT_001: AccountState.CONNECTED,
            FunctionId.EMP_CONNECT_002: AccountState.NOT_CONNECTED,
            FunctionId.EMP_INVITE_002: AccountState.NOT_CONNECTED,
        }
        if action.function_id in account_states:
            return self._items[employee_id].account_state is account_states[action.function_id]
        if action.function_id is FunctionId.EMP_INVITE_001:
            return (
                action.idempotency_key in self._effect_events
                and self._items[employee_id].account_state is AccountState.INVITED
            )
        employee_states = {
            FunctionId.EMP_STATUS_001: EmployeeState.INACTIVE,
            FunctionId.EMP_STATUS_002: EmployeeState.ACTIVE,
        }
        if action.function_id in employee_states:
            return self._items[employee_id].employee_state is employee_states[action.function_id]
        if action.function_id is FunctionId.EMP_RBAC_002:
            role_name = action.parameters.get("role_name")
            enabled = action.parameters.get("enabled")
            return (
                isinstance(role_name, str)
                and isinstance(enabled, bool)
                and sum(
                    role.name.casefold() == role_name.casefold() and role.enabled is enabled
                    for role in self._roles[employee_id].roles
                )
                == 1
            )
        if action.function_id is FunctionId.EMP_CONTRACT_002:
            contract_target = self._effect_targets.get(action.idempotency_key)
            contract_matches = [
                contract
                for contract in self._contracts.get(employee_id, ())
                if contract.contract_id == contract_target
            ]
            return len(contract_matches) == 1 and all(
                key == "contract_id" or self._matches(getattr(contract_matches[0], key), value)
                for key, value in action.parameters.items()
            )
        if action.function_id is FunctionId.EMP_CONTRACT_003:
            deleted_contract_id = action.parameters.get("contract_id")
            return all(
                contract.contract_id != deleted_contract_id
                for contract in self._contracts.get(employee_id, ())
            )
        if action.function_id is FunctionId.EMP_MAT_002:
            maturation_target = self._effect_targets.get(action.idempotency_key)
            maturation_matches = [
                maturation
                for maturation in self._maturations.get(employee_id, ())
                if maturation.maturation_id == maturation_target
            ]
            return len(maturation_matches) == 1 and all(
                self._matches(getattr(maturation_matches[0], key), value)
                for key, value in action.parameters.items()
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
                return False
            try:
                expected = canonical_decimal_text(amount)
            except ValueError:
                return False
            return (
                self._balance_corrections.get((employee_id, year, month, category.casefold()))
                == expected
            )
        if action.function_id is FunctionId.EMP_DOC_002:
            uploaded_document_id = self._effect_targets.get(action.idempotency_key)
            uploaded_matches = [
                document
                for document in self._documents.get(employee_id, ())
                if document.document_id == uploaded_document_id
            ]
            return len(uploaded_matches) == 1 and all(
                key in _UPLOAD_INTERNAL_FIELDS
                or key == "upload_id"
                or self._matches(getattr(uploaded_matches[0], key), value)
                for key, value in action.parameters.items()
            )
        if action.function_id is FunctionId.EMP_DOC_003:
            return action.idempotency_key in self._effect_events
        if action.function_id is FunctionId.EMP_DOC_004:
            updated_document_id = action.parameters.get("document_id")
            updated_matches = [
                document
                for document in self._documents.get(employee_id, ())
                if document.document_id == updated_document_id
            ]
            return len(updated_matches) == 1 and all(
                key == "document_id" or self._matches(getattr(updated_matches[0], key), value)
                for key, value in action.parameters.items()
            )
        if action.function_id is FunctionId.EMP_DOC_005:
            deleted_document_id = action.parameters.get("document_id")
            return all(
                document.document_id != deleted_document_id
                for document in self._documents.get(employee_id, ())
            )
        return False

    def _state_marker(self) -> str:
        """Canonical synthetic state marker used only to report whether state changed."""

        return json.dumps(
            {
                "items": {
                    key: value.model_dump(mode="json") for key, value in sorted(self._items.items())
                },
                "summaries": {
                    key: value.model_dump(mode="json")
                    for key, value in sorted(self._summaries.items())
                },
                "raw_summaries": self._raw_summaries,
                "contracts": {
                    key: [record.model_dump(mode="json") for record in value]
                    for key, value in sorted(self._contracts.items())
                },
                "roles": {
                    key: value.model_dump(mode="json") for key, value in sorted(self._roles.items())
                },
                "maturations": {
                    key: [record.model_dump(mode="json") for record in value]
                    for key, value in sorted(self._maturations.items())
                },
                "documents": {
                    key: [record.model_dump(mode="json") for record in value]
                    for key, value in sorted(self._documents.items())
                },
                "notifications": {
                    str(key): value.model_dump(mode="json")
                    for key, value in sorted(self._notifications.items())
                },
                "corrections": [
                    [list(key), value] for key, value in sorted(self._balance_corrections.items())
                ],
                "events": sorted(self._effect_events),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    async def execute_prepared(self, action: PreparedAction) -> ExecutionResult:
        self._require_open()
        existing = self._executions.get(action.idempotency_key)
        if existing is not None:
            return existing
        if action.function_id not in MUTATING_FUNCTIONS | FORBIDDEN_FUNCTIONS:
            raise DicValidationError("read functions cannot be executed as prepared writes")
        before_state = self._state_marker()
        employee_id = action.employee_id
        details: dict[str, JsonValue] = {}
        if action.function_id is FunctionId.EMP_CREATE_001:
            created_employee_id = self._execute_create(action)
            self._effect_targets[action.idempotency_key] = created_employee_id
            details["employee_id"] = created_employee_id
        elif action.function_id is FunctionId.EMP_EXPORT_001:
            if set(action.parameters).difference({"scope", "year"}):
                raise DicValidationError("export contains unsupported parameters")
            self._effect_events.add(action.idempotency_key)
            details["artifact_id"] = f"EXPORT-{action.request_fingerprint[:12].upper()}"
        elif action.function_id is FunctionId.EMP_NOTIF_002:
            notification_id = action.parameters.get("notification_id")
            read = action.parameters.get("read")
            if (
                not isinstance(notification_id, int)
                or isinstance(notification_id, bool)
                or not isinstance(read, bool)
                or notification_id not in self._notifications
            ):
                raise DicValidationError("notification state parameters are invalid")
            current = self._notifications[notification_id]
            if current.read is read:
                raise DicValidationError("notification already has the requested read state")
            self._notifications[notification_id] = current.model_copy(update={"read": read})
        else:
            if employee_id is None:
                raise DicValidationError("employee_id is required")
            self._require_employee(employee_id)
            self._execute_employee_write(action, employee_id)
            self._execute_related_write(action, employee_id)
            if action.function_id is FunctionId.EMP_DOC_003:
                details["artifact_id"] = f"DOCUMENT-{action.request_fingerprint[:12].upper()}"
        if not self._postcondition_applied(action):
            raise DicValidationError("synthetic postcondition could not be verified")
        changed = self._state_marker() != before_state and action.function_id not in {
            FunctionId.EMP_DOC_003,
            FunctionId.EMP_EXPORT_001,
        }
        result = ExecutionResult(
            action_id=action.action_id,
            function_id=action.function_id,
            status=OperationStatus.SUCCEEDED,
            changed=changed,
            postcondition_verified=True,
            message=(
                "synthetic protected artifact prepared"
                if not changed
                else "synthetic operation applied"
            ),
            correlation_id=action.correlation_id,
            details=details,
        )
        self._executions[action.idempotency_key] = result
        return result

    async def reconcile(self, action: PreparedAction) -> ReconciliationResult:
        result = self._executions.get(action.idempotency_key)
        if result is None:
            return ReconciliationResult(
                action_id=action.action_id,
                state=ReconciliationState.CONFIRMED_NOT_APPLIED,
                detail="idempotency key has no synthetic execution",
            )
        verified = self._postcondition_applied(action)
        return ReconciliationResult(
            action_id=action.action_id,
            state=(
                ReconciliationState.CONFIRMED_APPLIED if verified else ReconciliationState.UNKNOWN
            ),
            detail=(
                "synthetic postcondition is currently present"
                if verified
                else "synthetic state no longer proves the recorded postcondition"
            ),
        )

    async def close(self) -> None:
        self._closed = True
