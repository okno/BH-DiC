"""Deterministic, synthetic adapter used by tests and safe mock mode."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from bh_dic.dic.catalog import FORBIDDEN_FUNCTIONS, MUTATING_FUNCTIONS
from bh_dic.dic.errors import DicNotFoundError, DicValidationError
from bh_dic.dic.models import (
    AccountState,
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


class MockDicAdapter:
    """In-memory adapter containing no production identifiers or personal data."""

    def __init__(self) -> None:
        self._closed = False
        self._authenticated = True
        self._items: dict[str, EmployeeListItem] = {}
        self._summaries: dict[str, EmployeeSummary] = {}
        self._contracts: dict[str, list[ContractRecord]] = {}
        self._roles: dict[str, RolesResult] = {}
        self._time_access: dict[str, TimeAccessResult] = {}
        self._maturations: dict[str, list[MaturationRecord]] = {}
        self._balances: dict[tuple[str, int], BalanceResult] = {}
        self._payrolls: dict[str, list[PayrollMetadata]] = {}
        self._documents: dict[str, list[DocumentMetadata]] = {}
        self._executions: dict[str, ExecutionResult] = {}
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
        self._contracts[employee_id] = [
            ContractRecord(
                contract_id="CON-SYNTH-001",
                employee_id=employee_id,
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
                title_redacted="Documento sintetico [REDACTED]",
                category="CV",
                expiry_date=None,
                uploaded_at="2026-01-15",
                uploaded_by_redacted="A. A.",
                state="uploaded",
            )
        ]

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

    async def get_payroll_metadata(
        self, employee_id: str, year: int | None = None
    ) -> tuple[PayrollMetadata, ...]:
        self._require_employee(employee_id)
        records = self._payrolls.get(employee_id, ())
        return tuple(record for record in records if year is None or record.year == year)

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
        if action.function_id is FunctionId.EMP_UPDATE_001:
            summary = self._summaries[employee_id]
            allowed = {
                "payroll_number",
                "job_title",
                "workplace",
                "first_name",
                "last_name",
            }
            unknown = set(action.parameters).difference(allowed)
            if unknown:
                raise DicValidationError(f"unsupported employee fields: {sorted(unknown)}")
            updates: dict[str, JsonValue] = {}
            for key in ("payroll_number", "job_title", "workplace"):
                value = action.parameters.get(key)
                if value is not None:
                    updates[key] = value
            for source, destination in (
                ("first_name", "first_name_redacted"),
                ("last_name", "last_name_redacted"),
            ):
                value = action.parameters.get(source)
                if isinstance(value, str) and value:
                    updates[destination] = f"{value[0].upper()}."
            self._summaries[employee_id] = EmployeeSummary.model_validate(
                {**summary.model_dump(), **updates}
            )
        elif action.function_id is FunctionId.EMP_CONNECT_001:
            self._set_account_state(employee_id, AccountState.CONNECTED)
        elif action.function_id is FunctionId.EMP_CONNECT_002:
            self._set_account_state(employee_id, AccountState.NOT_CONNECTED)
        elif action.function_id is FunctionId.EMP_INVITE_001:
            self._set_account_state(employee_id, AccountState.INVITED)
        elif action.function_id is FunctionId.EMP_INVITE_002:
            self._set_account_state(employee_id, AccountState.NOT_CONNECTED)
        elif action.function_id is FunctionId.EMP_STATUS_001:
            self._set_employee_state(employee_id, EmployeeState.INACTIVE)
        elif action.function_id is FunctionId.EMP_STATUS_002:
            self._set_employee_state(employee_id, EmployeeState.ACTIVE)
        elif action.function_id is FunctionId.EMP_RBAC_002:
            roles_value = action.parameters.get("roles", [])
            if not isinstance(roles_value, list):
                raise DicValidationError("roles must be a list of strings")
            role_names = [value for value in roles_value if isinstance(value, str)]
            if len(role_names) != len(roles_value):
                raise DicValidationError("roles must be a list of strings")
            self._roles[employee_id] = RolesResult(
                employee_id=employee_id,
                roles=tuple(RoleAssignment(name=value, enabled=True) for value in role_names),
            )
            time_fields = {
                "timestamping_enabled",
                "attendance_sheet_access",
                "shift_management",
                "expense_access",
            }
            time_updates = {
                key: value
                for key, value in action.parameters.items()
                if key in time_fields and isinstance(value, bool)
            }
            if time_updates:
                current = self._time_access.get(
                    employee_id, TimeAccessResult(employee_id=employee_id)
                )
                self._time_access[employee_id] = TimeAccessResult.model_validate(
                    {**current.model_dump(), **time_updates}
                )
        elif action.function_id is FunctionId.EMP_DELETE_001:
            for store in (
                self._items,
                self._summaries,
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

    def _execute_create(self, action: PreparedAction) -> str:
        employee_id = self._string_parameter(action, "employee_id", required=False)
        employee_id = employee_id or f"EMP-MOCK-{action.request_fingerprint[:8].upper()}"
        if employee_id in self._items:
            raise DicValidationError("employee already exists")
        display_name = self._string_parameter(action, "display_name_redacted", required=False)
        display_name = display_name or "N. E."
        self._items[employee_id] = EmployeeListItem(
            employee_id=employee_id,
            display_name_redacted=display_name,
            employee_state=EmployeeState.ACTIVE,
            account_state=AccountState.NOT_CONNECTED,
        )
        self._summaries[employee_id] = EmployeeSummary(
            employee_id=employee_id,
            first_name_redacted="N.",
            last_name_redacted="E.",
            state=EmployeeState.ACTIVE,
        )
        return employee_id

    def _execute_related_write(self, action: PreparedAction, employee_id: str) -> None:
        suffix = action.request_fingerprint[:12].upper()
        if action.function_id is FunctionId.EMP_CONTRACT_002:
            contract_id = self._string_parameter(action, "contract_id", required=False)
            contract_record = ContractRecord(
                contract_id=contract_id or f"CON-{suffix}",
                employee_id=employee_id,
                schedule=self._string_parameter(action, "schedule", required=False),
                description=self._string_parameter(action, "description", required=False),
                status="active",
            )
            contracts = self._contracts.setdefault(employee_id, [])
            if contract_id is None:
                contracts.append(contract_record)
            else:
                for index, existing in enumerate(contracts):
                    if existing.contract_id == contract_id:
                        contracts[index] = contract_record
                        break
                else:
                    contracts.append(contract_record)
        elif action.function_id is FunctionId.EMP_CONTRACT_003:
            contract_id = self._string_parameter(action, "contract_id")
            contracts = self._contracts.get(employee_id, [])
            remaining_contracts = [
                contract for contract in contracts if contract.contract_id != contract_id
            ]
            if len(remaining_contracts) == len(contracts):
                raise DicNotFoundError("contract not found")
            self._contracts[employee_id] = remaining_contracts
        elif action.function_id is FunctionId.EMP_MAT_002:
            category = self._string_parameter(action, "category")
            self._maturations.setdefault(employee_id, []).append(
                MaturationRecord(
                    maturation_id=f"MAT-{suffix}",
                    employee_id=employee_id,
                    category=cast(str, category),
                    valid_from=self._string_parameter(action, "valid_from", required=False),
                    valid_to=self._string_parameter(action, "valid_to", required=False),
                    status="valid",
                )
            )
        elif action.function_id is FunctionId.EMP_DOC_002:
            category = self._string_parameter(action, "category", required=False)
            self._documents.setdefault(employee_id, []).append(
                DocumentMetadata(
                    document_id=f"DOC-{suffix}",
                    employee_id=employee_id,
                    title_redacted="Documento caricato [REDACTED]",
                    category=category,
                    state="uploaded",
                )
            )
        elif action.function_id is FunctionId.EMP_BAL_002:
            year = action.parameters.get("year")
            category = self._string_parameter(action, "category")
            amount = self._string_parameter(action, "amount")
            if not isinstance(year, int) or isinstance(year, bool):
                raise DicValidationError("year must be an integer")
            balance = self._balances.get(
                (employee_id, year), BalanceResult(employee_id=employee_id, year=year, lines=())
            )
            lines = list(balance.lines)
            for index, line in enumerate(lines):
                if line.category == category:
                    lines[index] = BalanceLine.model_validate(
                        {**line.model_dump(), "corrections": amount}
                    )
                    break
            else:
                lines.append(BalanceLine(category=category or "unknown", corrections=amount))
            self._balances[(employee_id, year)] = BalanceResult(
                employee_id=employee_id, year=year, lines=tuple(lines)
            )
        elif action.function_id is FunctionId.EMP_DOC_004:
            document_id = self._string_parameter(action, "document_id")
            category = self._string_parameter(action, "category", required=False)
            document_records = self._documents.get(employee_id, [])
            found = False
            for index, document_record in enumerate(document_records):
                if document_record.document_id == document_id:
                    document_records[index] = DocumentMetadata.model_validate(
                        {
                            **document_record.model_dump(),
                            "category": category or document_record.category,
                        }
                    )
                    found = True
                    break
            if not found:
                raise DicNotFoundError("document not found")
        elif action.function_id is FunctionId.EMP_DOC_005:
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

    async def execute_prepared(self, action: PreparedAction) -> ExecutionResult:
        self._require_open()
        existing = self._executions.get(action.idempotency_key)
        if existing is not None:
            return existing
        if action.function_id not in MUTATING_FUNCTIONS | FORBIDDEN_FUNCTIONS:
            raise DicValidationError("read functions cannot be executed as prepared writes")
        employee_id = action.employee_id
        details: dict[str, JsonValue] = {}
        if action.function_id is FunctionId.EMP_CREATE_001:
            details["employee_id"] = self._execute_create(action)
        elif action.function_id is FunctionId.EMP_EXPORT_001:
            details["artifact_id"] = f"EXPORT-{action.request_fingerprint[:12].upper()}"
        else:
            if employee_id is None:
                raise DicValidationError("employee_id is required")
            self._require_employee(employee_id)
            self._execute_employee_write(action, employee_id)
            self._execute_related_write(action, employee_id)
            if action.function_id is FunctionId.EMP_DOC_003:
                details["artifact_id"] = f"DOCUMENT-{action.request_fingerprint[:12].upper()}"
        changed = action.function_id not in {
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
        return ReconciliationResult(
            action_id=action.action_id,
            state=ReconciliationState.CONFIRMED_APPLIED,
            detail="synthetic execution record and postcondition are present",
        )

    async def close(self) -> None:
        self._closed = True
