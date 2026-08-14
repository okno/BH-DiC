"""Page Objects for every supported route under the DIC Employees menu."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from bh_dic.dic.errors import DicNotFoundError, DicUiChangedError, DicValidationError
from bh_dic.dic.models import (
    AccountState,
    BalanceLine,
    BalanceResult,
    ContractRecord,
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
    RoleAssignment,
    RolesResult,
    SortDirection,
    TimeAccessResult,
)
from bh_dic.dic.pages.base import BaseDicPage, LocatorLike


def _stable_record_id(prefix: str, *values: str | None) -> str:
    payload = "\x1f".join(value or "" for value in values)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _parameter_text(
    parameters: dict[str, JsonValue], key: str, *, required: bool = False
) -> str | None:
    value = parameters.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DicValidationError(f"parameter {key!r} must be a non-empty string")
    return value.strip()


def _account_state(value: str | None) -> AccountState:
    normalized = (value or "").strip().casefold()
    if "non collegato" in normalized:
        return AccountState.NOT_CONNECTED
    if "collegato" in normalized:
        return AccountState.CONNECTED
    if "invitato" in normalized:
        return AccountState.INVITED
    return AccountState.UNKNOWN


def _employee_state(value: str | None) -> EmployeeState:
    normalized = (value or "").strip().casefold()
    if any(token in normalized for token in ("disattiv", "inactive", "non attiv")):
        return EmployeeState.INACTIVE
    if any(token in normalized for token in ("attiv", "active")):
        return EmployeeState.ACTIVE
    return EmployeeState.UNKNOWN


class LoginPage(BaseDicPage):
    route_template = "/it/login"


class EmployeesListPage(BaseDicPage):
    route_template = "/it/app/employees/list"

    async def _employee_id(self, row: LocatorLike) -> str:
        value = await self.read_attribute("row.employee_id", "data-employee-id", root=row)
        if value:
            return self.validate_employee_id(value)
        href = await self.read_attribute("row.employee_id", "href", root=row)
        match = re.search(r"/employees/info/([^/]+)/", href or "")
        if match is None:
            raise DicUiChangedError("employee row has no stable employee identifier")
        return self.validate_employee_id(match.group(1))

    async def _read_row(self, row: LocatorLike) -> EmployeeListItem:
        employee_id = await self._employee_id(row)
        name = await self.read_text("row.name", root=row)
        return EmployeeListItem(
            employee_id=employee_id,
            display_name_redacted=self.redact_name(name) or "[REDACTED]",
            email_redacted=self.redact_email(await self.read_text("row.email", root=row)),
            tax_code_redacted=self.redact_tail(await self.read_text("row.tax_code", root=row)),
            job_title=await self.read_text("row.job_title", root=row),
            group_name=await self.read_text("row.group", root=row),
            payroll_number=await self.read_text("row.payroll_number", root=row),
            contract_label=await self.read_text("row.contract", root=row),
            contract_state=await self.read_text("row.contract_state", root=row),
            contract_period=await self.read_text("row.contract_period", root=row),
            schedule_model=await self.read_text("row.schedule", root=row),
            workplace=await self.read_text("row.workplace", root=row),
            account_state=_account_state(await self.read_text("row.account_state", root=row)),
            employee_state=_employee_state(await self.read_text("row.employee_state", root=row)),
        )

    async def list(self, query: EmployeeListQuery) -> EmployeeListResult:
        await self.open()
        if query.query:
            await self.fill("employees.search", query.query)
        await self.click(f"employees.filter.{query.employee_filter.value}")
        sort_key = f"employees.sort.{query.sort_by}"
        sort_control = await self.locate(sort_key)
        if sort_control is None:
            raise DicUiChangedError("employee sort control is unavailable")
        aria_sort = (await sort_control.get_attribute("aria-sort") or "").casefold()
        target = "ascending" if query.sort_direction is SortDirection.ASC else "descending"
        if aria_sort not in {"none", "ascending", "descending"}:
            raise DicUiChangedError("employee sort state cannot be verified")
        for _ in range(2):
            if aria_sort == target:
                break
            await sort_control.click()
            aria_sort = (await sort_control.get_attribute("aria-sort") or "").casefold()
        if aria_sort != target:
            raise DicUiChangedError("employee sort did not reach the requested state")
        for _ in range(query.page - 1):
            next_button = await self.locate("employees.next", required=False)
            if next_button is None:
                break
            disabled = await next_button.get_attribute("disabled")
            aria_disabled = await next_button.get_attribute("aria-disabled")
            if disabled is not None or aria_disabled == "true":
                break
            await next_button.click()
        rows = await self.all_matches("employees.rows")
        row_count = await rows.count()
        items = tuple([await self._read_row(rows.nth(index)) for index in range(row_count)])
        total_text = await self.read_text("employees.total")
        total_matches = re.findall(r"\d+", (total_text or "").replace(".", ""))
        total = int(total_matches[-1]) if total_matches else row_count
        next_button = await self.locate("employees.next", required=False)
        has_next = False
        if next_button is not None:
            has_next = (
                await next_button.get_attribute("disabled") is None
                and await next_button.get_attribute("aria-disabled") != "true"
            )
        return EmployeeListResult(
            items=items,
            page=query.page,
            page_size=query.page_size,
            total=total,
            has_next=has_next,
        )

    async def create_employee(self, action: PreparedAction) -> None:
        await self.open()
        await self.click("employees.new")
        mode = _parameter_text(action.parameters, "creation_mode") or "manual"
        if mode != "manual":
            raise DicValidationError("only deterministic manual creation is implemented")
        await self.click("employees.create_manual")
        allowed = {
            "first_name": "summary.first_name",
            "last_name": "summary.last_name",
            "payroll_number": "summary.payroll_number",
            "tax_code": "summary.tax_code",
            "birth_date": "summary.birth_date",
            "iban": "summary.iban",
            "job_title": "summary.job_title",
            "phone": "summary.phone",
            "business_email": "summary.email",
            "address": "summary.address",
            "workplace": "summary.workplace",
            "notes": "summary.notes",
        }
        unexpected = set(action.parameters).difference(set(allowed) | {"creation_mode"})
        if unexpected:
            raise DicValidationError(f"unsupported create fields: {sorted(unexpected)}")
        if not _parameter_text(action.parameters, "first_name"):
            raise DicValidationError("first_name is required")
        if not _parameter_text(action.parameters, "last_name"):
            raise DicValidationError("last_name is required")
        for parameter, selector_key in allowed.items():
            value = _parameter_text(action.parameters, parameter)
            if value is not None:
                await self.fill(selector_key, value)
        await self.click("employees.create_save")
        await self.confirm_if_present()


class EmployeeSummaryPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/summary"

    async def read(self, employee_id: str) -> EmployeeSummary:
        await self.open(employee_id)
        return EmployeeSummary(
            employee_id=employee_id,
            first_name_redacted=self.redact_name(await self.read_text("summary.first_name")),
            last_name_redacted=self.redact_name(await self.read_text("summary.last_name")),
            payroll_number=await self.read_text("summary.payroll_number"),
            tax_code_redacted=self.redact_tail(await self.read_text("summary.tax_code")),
            birth_date_redacted=self.redact_tail(await self.read_text("summary.birth_date"), 2),
            iban_redacted=self.redact_tail(await self.read_text("summary.iban")),
            job_title=await self.read_text("summary.job_title"),
            phone_redacted=self.redact_tail(await self.read_text("summary.phone")),
            business_email_redacted=self.redact_email(await self.read_text("summary.email")),
            address_redacted=("[REDACTED]" if await self.read_text("summary.address") else None),
            workplace=await self.read_text("summary.workplace"),
            notes_redacted=("[REDACTED]" if await self.read_text("summary.notes") else None),
            state=_employee_state(await self.read_text("summary.state")),
        )

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        if action.function_id is FunctionId.EMP_UPDATE_001:
            allowed = {
                "first_name": "summary.first_name",
                "last_name": "summary.last_name",
                "payroll_number": "summary.payroll_number",
                "tax_code": "summary.tax_code",
                "birth_date": "summary.birth_date",
                "iban": "summary.iban",
                "job_title": "summary.job_title",
                "phone": "summary.phone",
                "business_email": "summary.email",
                "address": "summary.address",
                "workplace": "summary.workplace",
                "notes": "summary.notes",
            }
            unexpected = set(action.parameters).difference(allowed)
            if unexpected:
                raise DicValidationError(f"unsupported update fields: {sorted(unexpected)}")
            for parameter, selector_key in allowed.items():
                value = _parameter_text(action.parameters, parameter)
                if value is not None:
                    await self.fill(selector_key, value)
            await self.click("summary.save")
        else:
            controls = {
                FunctionId.EMP_CONNECT_001: "summary.connect",
                FunctionId.EMP_CONNECT_002: "summary.disconnect",
                FunctionId.EMP_INVITE_001: "summary.invite_again",
                FunctionId.EMP_INVITE_002: "summary.cancel_invite",
                FunctionId.EMP_STATUS_001: "summary.deactivate",
                FunctionId.EMP_STATUS_002: "summary.activate",
                FunctionId.EMP_DELETE_001: "summary.delete",
            }
            try:
                selector_key = controls[action.function_id]
            except KeyError as exc:
                raise DicValidationError("function is not handled by summary page") from exc
            await self.click(selector_key)
        await self.confirm_if_present()


class EmployeeRolesPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/roles"

    async def read_roles(self, employee_id: str) -> RolesResult:
        await self.open(employee_id)
        group_nodes = await self.all_matches("roles.groups")
        group_names: list[str] = []
        for index in range(await group_nodes.count()):
            group_name = (await group_nodes.nth(index).inner_text()).strip()
            if group_name:
                group_names.append(group_name)
        groups = tuple(group_names)
        role_nodes = await self.all_matches("roles.items")
        roles: list[RoleAssignment] = []
        for index in range(await role_nodes.count()):
            node = role_nodes.nth(index)
            name = (await node.inner_text()).strip()
            if name:
                enabled = (await node.get_attribute("aria-checked")) == "true"
                roles.append(RoleAssignment(name=name, enabled=enabled))
        return RolesResult(employee_id=employee_id, groups=groups, roles=tuple(roles))

    async def read_time_access(self, employee_id: str) -> TimeAccessResult:
        await self.open(employee_id)
        return TimeAccessResult(
            employee_id=employee_id,
            timestamping_enabled=await self.is_checked("roles.time.timestamping"),
            attendance_sheet_access=await self.is_checked("roles.time.attendance"),
            shift_management=await self.is_checked("roles.time.shifts"),
            expense_access=await self.is_checked("roles.time.expenses"),
        )

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        field_map = {
            "timestamping_enabled": "roles.time.timestamping",
            "attendance_sheet_access": "roles.time.attendance",
            "shift_management": "roles.time.shifts",
            "expense_access": "roles.time.expenses",
        }
        unexpected = set(action.parameters).difference(field_map)
        if unexpected:
            raise DicValidationError(f"unsupported role fields: {sorted(unexpected)}")
        for parameter, selector_key in field_map.items():
            value = action.parameters.get(parameter)
            if value is None:
                continue
            if not isinstance(value, bool):
                raise DicValidationError(f"{parameter} must be boolean")
            locator = await self.locate(selector_key)
            if locator is None:
                raise DicUiChangedError("role control is unavailable")
            await locator.set_checked(value)
        await self.click("roles.save")
        await self.confirm_if_present()


class TimestampEmployeesPage(BaseDicPage):
    route_template = "/it/app/settings/timestamps/employees"

    async def read_enabled(self, employee_id: str) -> bool | None:
        self.validate_employee_id(employee_id)
        await self.open()
        rows = await self.all_matches("timestamps.rows")
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed_id = await self.read_attribute(
                "timestamps.row.employee_id", "data-employee-id", root=row
            )
            if observed_id != employee_id:
                continue
            enabled = await self.locate("timestamps.row.enabled", root=row, required=False)
            if enabled is None:
                return None
            try:
                return await enabled.is_checked()
            except Exception:
                aria_checked = await enabled.get_attribute("aria-checked")
                return None if aria_checked is None else aria_checked == "true"
        return None


class EmployeeContractsPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/contracts"

    async def _find_row(self, contract_id: str) -> LocatorLike:
        rows = await self.all_matches("contracts.rows")
        for index in range(await rows.count()):
            row = rows.nth(index)
            value = await self.read_attribute("contract_row.id", "data-contract-id", root=row)
            if value == contract_id:
                return row
        raise DicNotFoundError("contract row not found")

    async def read(self, employee_id: str) -> tuple[ContractRecord, ...]:
        await self.open(employee_id)
        rows = await self.all_matches("contracts.rows")
        result: list[ContractRecord] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            values = {
                key: await self.read_text(f"contract_row.{key}", root=row)
                for key in (
                    "schedule",
                    "flexibility",
                    "permanent",
                    "start_date",
                    "end_date",
                    "ccnl_level",
                    "work_regime",
                    "description",
                    "type",
                    "status",
                    "period",
                )
            }
            contract_id = await self.read_attribute("contract_row.id", "data-contract-id", root=row)
            contract_id = contract_id or _stable_record_id(
                "CON", values["start_date"], values["type"], values["period"]
            )
            permanent_text = (values["permanent"] or "").casefold()
            permanent = None
            if permanent_text:
                permanent = any(word in permanent_text for word in ("sì", "si", "true", "yes"))
            result.append(
                ContractRecord(
                    contract_id=contract_id,
                    employee_id=employee_id,
                    schedule=values["schedule"],
                    flexibility=values["flexibility"],
                    permanent=permanent,
                    start_date=values["start_date"],
                    end_date=values["end_date"],
                    ccnl_level=values["ccnl_level"],
                    work_regime=values["work_regime"],
                    description="[REDACTED]" if values["description"] else None,
                    contract_type=values["type"],
                    status=values["status"],
                    period=values["period"],
                )
            )
        return tuple(result)

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        if action.function_id is FunctionId.EMP_CONTRACT_003:
            contract_id = _parameter_text(action.parameters, "contract_id", required=True)
            row = await self._find_row(contract_id or "")
            await self.click("contracts.delete", root=row)
            await self.confirm_if_present()
            return
        contract_id = _parameter_text(action.parameters, "contract_id")
        if contract_id:
            await self.click("contracts.edit", root=await self._find_row(contract_id))
        else:
            await self.click("contracts.new")
        allowed = {
            "schedule": "contracts.schedule",
            "flexibility": "contracts.flexibility",
            "start_date": "contracts.start_date",
            "end_date": "contracts.end_date",
            "ccnl_level": "contracts.ccnl_level",
            "work_regime": "contracts.work_regime",
            "description": "contracts.description",
            "contract_type": "contracts.type",
        }
        unexpected = set(action.parameters).difference(set(allowed) | {"contract_id", "permanent"})
        if unexpected:
            raise DicValidationError(f"unsupported contract fields: {sorted(unexpected)}")
        for parameter, selector_key in allowed.items():
            value = _parameter_text(action.parameters, parameter)
            if value is not None:
                await self.fill(selector_key, value)
        permanent = action.parameters.get("permanent")
        if permanent is not None:
            if not isinstance(permanent, bool):
                raise DicValidationError("permanent must be boolean")
            locator = await self.locate("contracts.permanent")
            if locator is None:
                raise DicUiChangedError("contract permanence control is unavailable")
            await locator.set_checked(permanent)
        await self.click("contracts.save")
        await self.confirm_if_present()


class EmployeeMaturationsPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/maturations"

    async def read(self, employee_id: str) -> tuple[MaturationRecord, ...]:
        await self.open(employee_id)
        rows = await self.all_matches("maturations.rows")
        result: list[MaturationRecord] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            category = await self.read_text("maturation_row.category", root=row)
            valid_from = await self.read_text("maturation_row.valid_from", root=row)
            valid_to = await self.read_text("maturation_row.valid_to", root=row)
            record_id = await self.read_attribute(
                "maturation_row.id", "data-maturation-id", root=row
            )
            result.append(
                MaturationRecord(
                    maturation_id=record_id
                    or _stable_record_id("MAT", category, valid_from, valid_to),
                    employee_id=employee_id,
                    category=category or "unknown",
                    valid_from=valid_from,
                    valid_to=valid_to,
                    status=await self.read_text("maturation_row.status", root=row),
                )
            )
        return tuple(result)

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        await self.click("maturations.new")
        allowed = {
            "category": "maturations.category",
            "valid_from": "maturations.valid_from",
            "valid_to": "maturations.valid_to",
        }
        unexpected = set(action.parameters).difference(allowed)
        if unexpected:
            raise DicValidationError(f"unsupported maturation fields: {sorted(unexpected)}")
        if not _parameter_text(action.parameters, "category", required=True):
            raise DicValidationError("category is required")
        for parameter, selector_key in allowed.items():
            value = _parameter_text(action.parameters, parameter)
            if value is not None:
                await self.fill(selector_key, value)
        await self.click("maturations.save")
        await self.confirm_if_present()


class EmployeeBalancePage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/counters"

    async def read(self, employee_id: str, year: int) -> BalanceResult:
        await self.open(employee_id)
        await self.select("balance.year", str(year))
        rows = await self.all_matches("balance.rows")
        lines: list[BalanceLine] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            lines.append(
                BalanceLine(
                    category=await self.read_text("balance_row.category", root=row) or "unknown",
                    previous_year=await self.read_text("balance_row.previous_year", root=row),
                    previous_month=await self.read_text("balance_row.previous_month", root=row),
                    accrued=await self.read_text("balance_row.accrued", root=row),
                    used=await self.read_text("balance_row.used", root=row),
                    corrections=await self.read_text("balance_row.corrections", root=row),
                    current_residual=await self.read_text("balance_row.current_residual", root=row),
                )
            )
        return BalanceResult(employee_id=employee_id, year=year, lines=tuple(lines))

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        year = action.parameters.get("year")
        if not isinstance(year, int) or isinstance(year, bool):
            raise DicValidationError("year must be an integer")
        await self.select("balance.year", str(year))
        await self.click("balance.correct")
        category = _parameter_text(action.parameters, "category", required=True)
        amount = _parameter_text(action.parameters, "amount", required=True)
        await self.fill("balance.category", category or "")
        await self.fill("balance.amount", amount or "")
        await self.click("balance.save")
        await self.confirm_if_present()


class EmployeePayrollsPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/payrolls"

    async def read(self, employee_id: str, year: int | None = None) -> tuple[PayrollMetadata, ...]:
        await self.open(employee_id)
        if year is not None:
            await self.select("payrolls.year", str(year))
        rows = await self.all_matches("payrolls.rows")
        result: list[PayrollMetadata] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            year_text = await self.read_text("payroll_row.year", root=row)
            month_text = await self.read_text("payroll_row.month", root=row)
            record_year = int(year_text) if year_text and year_text.isdigit() else year
            if record_year is None:
                raise DicUiChangedError("payroll metadata row does not expose its year")
            record_id = await self.read_attribute("payroll_row.id", "data-payroll-id", root=row)
            result.append(
                PayrollMetadata(
                    payroll_id=record_id
                    or _stable_record_id("PAY", year_text, month_text, str(index)),
                    employee_id=employee_id,
                    year=record_year,
                    month=int(month_text) if month_text and month_text.isdigit() else None,
                    status=await self.read_text("payroll_row.status", root=row),
                    published_at=await self.read_text("payroll_row.published_at", root=row),
                )
            )
        return tuple(result)


class EmployeeDocumentsPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/documents/list"

    async def _find_row(self, document_id: str) -> LocatorLike:
        rows = await self.all_matches("documents.rows")
        for index in range(await rows.count()):
            row = rows.nth(index)
            value = await self.read_attribute("document_row.id", "data-document-id", root=row)
            if value == document_id:
                return row
        raise DicNotFoundError("document row not found")

    async def read(self, employee_id: str, query: DocumentQuery) -> tuple[DocumentMetadata, ...]:
        await self.open(employee_id)
        if query.state == "uploaded":
            await self.click("documents.uploaded")
        elif query.state == "pending":
            await self.click("documents.pending")
        if query.query:
            await self.fill("documents.search", query.query)
        rows = await self.all_matches("documents.rows")
        result: list[DocumentMetadata] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            title = await self.read_text("document_row.title", root=row)
            category = await self.read_text("document_row.category", root=row)
            if query.category and category != query.category:
                continue
            state_text = (await self.read_text("document_row.state", root=row) or "").casefold()
            state: Literal["uploaded", "pending", "unknown"] = (
                "pending" if "attesa" in state_text or "pending" in state_text else "uploaded"
            )
            expiry_date = await self.read_text("document_row.expiry", root=row)
            uploaded_at = await self.read_text("document_row.uploaded_at", root=row)
            record_id = await self.read_attribute("document_row.id", "data-document-id", root=row)
            result.append(
                DocumentMetadata(
                    document_id=record_id
                    or _stable_record_id("DOC", category, expiry_date, uploaded_at, str(index)),
                    employee_id=employee_id,
                    title_redacted="[REDACTED]" if title else "untitled [REDACTED]",
                    category=category,
                    expiry_date=expiry_date,
                    uploaded_at=uploaded_at,
                    uploaded_by_redacted=self.redact_name(
                        await self.read_text("document_row.uploaded_by", root=row)
                    ),
                    state=state,
                )
            )
        return tuple(result)

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        if action.function_id is FunctionId.EMP_DOC_002:
            safe_path = _parameter_text(action.parameters, "safe_local_path", required=True)
            path = Path(safe_path or "")
            if not path.is_absolute() or not await asyncio.to_thread(path.is_file):
                raise DicValidationError("safe_local_path must be an existing absolute file")
            await self.click("documents.upload")
            file_input = await self.locate("documents.file")
            if file_input is None:
                raise DicUiChangedError("document file input is unavailable")
            await file_input.set_input_files(str(path))
            title = _parameter_text(action.parameters, "title")
            category = _parameter_text(action.parameters, "category")
            expiry = _parameter_text(action.parameters, "expiry_date")
            if title:
                await self.fill("documents.title", title)
            if category:
                await self.fill("documents.category", category)
            if expiry:
                await self.fill("documents.expiry", expiry)
            await self.click("documents.save")
        elif action.function_id is FunctionId.EMP_DOC_004:
            document_id = _parameter_text(action.parameters, "document_id", required=True)
            await self.click("documents.edit", root=await self._find_row(document_id or ""))
            category = _parameter_text(action.parameters, "category")
            expiry = _parameter_text(action.parameters, "expiry_date")
            if category:
                await self.fill("documents.category", category)
            if expiry:
                await self.fill("documents.expiry", expiry)
            await self.click("documents.save")
        elif action.function_id is FunctionId.EMP_DOC_005:
            document_id = _parameter_text(action.parameters, "document_id", required=True)
            await self.click("documents.delete", root=await self._find_row(document_id or ""))
        else:
            raise DicValidationError("function is not handled by documents page")
        await self.confirm_if_present()
