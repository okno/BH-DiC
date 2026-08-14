"""Page Objects for every supported route under the DIC Employees menu."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import JsonValue

from bh_dic.dic.errors import DicNotFoundError, DicUiChangedError, DicValidationError
from bh_dic.dic.models import (
    AccountState,
    BalanceCorrectionState,
    BalanceLine,
    BalanceResult,
    ContractRecord,
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
    RoleAssignment,
    RolesResult,
    SortDirection,
    TimeAccessResult,
)
from bh_dic.dic.pages.base import BaseDicPage, LocatorLike, VerifiedUploadPayload
from bh_dic.dic.values import canonical_decimal_text


def _stable_record_id(prefix: str, *values: str | None) -> str:
    payload = "\x1f".join(value or "" for value in values)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _is_fallback_record_id(value: str, prefix: str) -> bool:
    return re.fullmatch(rf"{re.escape(prefix)}-[a-f0-9]{{16}}", value) is not None


def _expected_value_matches(actual: object, expected: JsonValue) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().casefold() == expected.strip().casefold()
    return actual == expected


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
        if await self.locate("employees.container", required=False) is None:
            raise DicUiChangedError("employee list container is unavailable")
        rows = await self.all_matches("employees.rows")
        row_count = await rows.count()
        items = tuple([await self._read_row(rows.nth(index)) for index in range(row_count)])
        total_text = await self.read_text("employees.total")
        total_matches = re.findall(r"\d+", (total_text or "").replace(".", ""))
        total = int(total_matches[-1]) if total_matches else row_count
        if row_count == 0 and (not total_matches or total != 0):
            raise DicUiChangedError("empty employee result has no verified zero total")
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
        first_name = _parameter_text(action.parameters, "first_name", required=True)
        last_name = _parameter_text(action.parameters, "last_name", required=True)
        for parameter, selector_key in allowed.items():
            value = _parameter_text(action.parameters, parameter)
            if value is not None:
                await self.fill(selector_key, value)
        await self.click("employees.create_save")
        await self.confirm_if_present(expected_identity=(first_name or "", last_name or ""))

    @staticmethod
    def _create_query(parameters: dict[str, JsonValue]) -> str:
        first_name = _parameter_text(parameters, "first_name", required=True)
        last_name = _parameter_text(parameters, "last_name", required=True)
        return f"{first_name} {last_name}"

    async def stable_employee_ids_for_create(
        self, parameters: dict[str, JsonValue]
    ) -> frozenset[str]:
        """Capture an exhaustive stable-ID baseline for the intended new employee."""

        result = await self.list(
            EmployeeListQuery(
                query=self._create_query(parameters),
                employee_filter=EmployeeFilter.ALL,
                page_size=100,
            )
        )
        if result.has_next:
            raise DicUiChangedError("employee create baseline is not exhaustive")
        return frozenset(item.employee_id for item in result.items)

    async def verify_created_employee(
        self,
        baseline_ids: frozenset[str],
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Verify exactly one new stable ID and raw observable values in-page."""

        allowed = {
            "first_name",
            "last_name",
            "payroll_number",
            "tax_code",
            "job_title",
            "business_email",
            "workplace",
            "creation_mode",
        }
        if set(parameters).difference(allowed):
            return None
        query = self._create_query(parameters)
        result = await self.list(
            EmployeeListQuery(
                query=query,
                employee_filter=EmployeeFilter.ALL,
                page_size=100,
            )
        )
        if result.has_next:
            return None
        rows = await self.all_matches("employees.rows")
        candidates: list[LocatorLike] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            employee_id = await self._employee_id(row)
            if employee_id not in baseline_ids:
                candidates.append(row)
        if len(candidates) != 1:
            return None
        row = candidates[0]
        raw_name = await self.read_text("row.name", root=row)
        if not _expected_value_matches(raw_name, query):
            return False
        selectors = {
            "payroll_number": "row.payroll_number",
            "tax_code": "row.tax_code",
            "job_title": "row.job_title",
            "business_email": "row.email",
            "workplace": "row.workplace",
        }
        for parameter, selector_key in selectors.items():
            expected = parameters.get(parameter)
            if expected is not None and not _expected_value_matches(
                await self.read_text(selector_key, root=row), expected
            ):
                return False
        return True


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

    async def verify_expected(
        self, employee_id: str, parameters: dict[str, JsonValue]
    ) -> bool | None:
        """Compare every approved update field in-page without returning raw PII."""

        selectors = {
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
        if not parameters or set(parameters).difference(selectors):
            return None
        await self.open(employee_id)
        for parameter, expected in parameters.items():
            if not isinstance(expected, str):
                return None
            observed = await self.read_text(selectors[parameter])
            if not _expected_value_matches(observed, expected):
                return False
        return True

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
            if not action.parameters:
                raise DicValidationError("employee update requires at least one field")
            for parameter, selector_key in allowed.items():
                value = _parameter_text(action.parameters, parameter)
                if value is not None:
                    await self.fill(selector_key, value)
            await self.click("summary.save")
        else:
            if action.parameters:
                raise DicValidationError("summary action does not accept parameters")
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
        await self.confirm_if_present(expected_identity=action.employee_id)


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
        role_name = _parameter_text(action.parameters, "role_name")
        if role_name is None or set(action.parameters) != {"role_name", "enabled"}:
            raise DicValidationError("role update requires only role_name and enabled")
        enabled = action.parameters.get("enabled")
        if not isinstance(enabled, bool):
            raise DicValidationError("enabled must be boolean")
        role_nodes = await self.all_matches("roles.items")
        matches: list[LocatorLike] = []
        for index in range(await role_nodes.count()):
            node = role_nodes.nth(index)
            if (await node.inner_text()).strip().casefold() == role_name.casefold():
                matches.append(node)
        if len(matches) != 1:
            raise DicValidationError("role_name must identify exactly one role")
        role = matches[0]
        try:
            current = await role.is_checked()
        except Exception:
            aria_checked = await role.get_attribute("aria-checked")
            if aria_checked not in {"true", "false"}:
                raise DicUiChangedError("role control state is unavailable") from None
            current = aria_checked == "true"
        if current is enabled:
            raise DicValidationError("role state already matches requested value")
        await role.set_checked(enabled)
        await self.click("roles.save")
        await self.confirm_if_present(expected_identity=(action.employee_id, role_name))


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

    async def _rows(self) -> LocatorLike:
        if await self.locate("contracts.container", required=False) is None:
            raise DicUiChangedError("contract list container is unavailable")
        return await self.all_matches("contracts.rows")

    async def _find_row(self, contract_id: str) -> LocatorLike:
        if _is_fallback_record_id(contract_id, "CON"):
            raise DicValidationError("fallback contract identifiers are not actionable")
        rows = await self._rows()
        for index in range(await rows.count()):
            row = rows.nth(index)
            value = await self.read_attribute("contract_row.id", "data-contract-id", root=row)
            if value == contract_id:
                return row
        raise DicNotFoundError("contract row not found")

    async def read(self, employee_id: str) -> tuple[ContractRecord, ...]:
        await self.open(employee_id)
        rows = await self._rows()
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
            stable_identifier = bool(contract_id)
            contract_id = contract_id or _stable_record_id(
                "CON", values["start_date"], values["type"], values["period"]
            )
            edit = await self.locate("contracts.edit", root=row, required=False)
            delete = await self.locate("contracts.delete", root=row, required=False)
            permanent_text = (values["permanent"] or "").casefold()
            permanent = None
            if permanent_text:
                permanent = any(word in permanent_text for word in ("sì", "si", "true", "yes"))
            result.append(
                ContractRecord(
                    contract_id=contract_id,
                    employee_id=employee_id,
                    stable_identifier=stable_identifier,
                    actionable=stable_identifier and edit is not None and delete is not None,
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

    async def stable_contract_ids(self, employee_id: str) -> frozenset[str]:
        """Return only DOM-stable identifiers for an in-process create baseline."""

        await self.open(employee_id)
        rows = await self._rows()
        identifiers: set[str] = set()
        for index in range(await rows.count()):
            value = await self.read_attribute(
                "contract_row.id", "data-contract-id", root=rows.nth(index)
            )
            if value:
                identifiers.add(value)
        return frozenset(identifiers)

    async def verify_created_contract(
        self,
        employee_id: str,
        baseline_ids: frozenset[str],
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Verify one new stable contract and all requested values after dispatch."""

        await self.open(employee_id)
        allowed = {
            "schedule": "schedule",
            "flexibility": "flexibility",
            "start_date": "start_date",
            "end_date": "end_date",
            "ccnl_level": "ccnl_level",
            "work_regime": "work_regime",
            "description": "description",
            "contract_type": "type",
            "permanent": "permanent",
        }
        expected = {key: value for key, value in parameters.items() if key in allowed}
        if not expected or set(parameters).difference(allowed):
            return None
        rows = await self._rows()
        candidates: list[LocatorLike] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed_id = await self.read_attribute("contract_row.id", "data-contract-id", root=row)
            if observed_id and observed_id not in baseline_ids:
                candidates.append(row)
        if len(candidates) != 1:
            return None
        row = candidates[0]
        observed: dict[str, object] = {}
        for parameter, selector_suffix in allowed.items():
            raw = await self.read_text(f"contract_row.{selector_suffix}", root=row)
            if parameter == "permanent":
                normalized = (raw or "").strip().casefold()
                observed[parameter] = (
                    None
                    if not normalized
                    else any(word in normalized for word in ("sì", "si", "true", "yes"))
                )
            else:
                observed[parameter] = raw
        return all(_expected_value_matches(observed[key], value) for key, value in expected.items())

    async def verify_expected(
        self,
        employee_id: str,
        contract_id: str | None,
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Compare raw contract values internally; never return contract text."""

        if contract_id is not None and _is_fallback_record_id(contract_id, "CON"):
            return None
        await self.open(employee_id)
        rows = await self._rows()
        allowed = {
            "schedule": "schedule",
            "flexibility": "flexibility",
            "start_date": "start_date",
            "end_date": "end_date",
            "ccnl_level": "ccnl_level",
            "work_regime": "work_regime",
            "description": "description",
            "contract_type": "type",
            "permanent": "permanent",
        }
        expected = {key: value for key, value in parameters.items() if key in allowed}
        if not expected or set(parameters).difference(set(allowed) | {"contract_id"}):
            return None
        if contract_id is None:
            return None
        matches = 0
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed_id = await self.read_attribute("contract_row.id", "data-contract-id", root=row)
            if contract_id is not None and observed_id != contract_id:
                continue
            if contract_id is not None and not observed_id:
                return None
            observed: dict[str, object] = {}
            for parameter, selector_suffix in allowed.items():
                raw = await self.read_text(f"contract_row.{selector_suffix}", root=row)
                if parameter == "permanent":
                    normalized = (raw or "").strip().casefold()
                    observed[parameter] = (
                        None
                        if not normalized
                        else any(word in normalized for word in ("sì", "si", "true", "yes"))
                    )
                else:
                    observed[parameter] = raw
            if all(
                _expected_value_matches(observed[key], value) for key, value in expected.items()
            ):
                matches += 1
        return matches == 1

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        if action.function_id is FunctionId.EMP_CONTRACT_003:
            if set(action.parameters) != {"contract_id"}:
                raise DicValidationError("contract delete requires only contract_id")
            contract_id = _parameter_text(action.parameters, "contract_id", required=True)
            row = await self._find_row(contract_id or "")
            await self.click("contracts.delete", root=row)
            await self.confirm_if_present(expected_identity=(action.employee_id, contract_id or ""))
            return
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
        if not set(action.parameters).intersection(set(allowed) | {"permanent"}):
            raise DicValidationError("contract write requires at least one editable field")
        contract_id = _parameter_text(action.parameters, "contract_id")
        if contract_id:
            await self.click("contracts.edit", root=await self._find_row(contract_id))
        else:
            await self.click("contracts.new")
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
        await self.confirm_if_present(
            expected_identity=(action.employee_id, contract_id or action.employee_id)
        )


class EmployeeMaturationsPage(BaseDicPage):
    route_template = "/it/app/employees/info/{employee_id}/maturations"

    async def _rows(self) -> LocatorLike:
        if await self.locate("maturations.container", required=False) is None:
            raise DicUiChangedError("maturation list container is unavailable")
        return await self.all_matches("maturations.rows")

    async def read(self, employee_id: str) -> tuple[MaturationRecord, ...]:
        await self.open(employee_id)
        rows = await self._rows()
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

    async def stable_maturation_ids(self, employee_id: str) -> frozenset[str]:
        """Return only DOM-stable identifiers for an in-process create baseline."""

        await self.open(employee_id)
        rows = await self._rows()
        identifiers: set[str] = set()
        for index in range(await rows.count()):
            value = await self.read_attribute(
                "maturation_row.id", "data-maturation-id", root=rows.nth(index)
            )
            if value:
                identifiers.add(value)
        return frozenset(identifiers)

    async def verify_created_maturation(
        self,
        employee_id: str,
        baseline_ids: frozenset[str],
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Verify one new stable maturation and all requested values."""

        allowed = {"category": "category", "valid_from": "valid_from", "valid_to": "valid_to"}
        expected = {key: value for key, value in parameters.items() if key in allowed}
        if (
            set(parameters).difference(allowed)
            or not isinstance(expected.get("category"), str)
            or not expected["category"]
        ):
            return None
        await self.open(employee_id)
        rows = await self._rows()
        candidates: list[LocatorLike] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed_id = await self.read_attribute(
                "maturation_row.id", "data-maturation-id", root=row
            )
            if observed_id and observed_id not in baseline_ids:
                candidates.append(row)
        if len(candidates) != 1:
            return None
        row = candidates[0]
        observed = {
            key: await self.read_text(f"maturation_row.{selector_suffix}", root=row)
            for key, selector_suffix in allowed.items()
        }
        return all(_expected_value_matches(observed[key], value) for key, value in expected.items())

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        allowed = {
            "category": "maturations.category",
            "valid_from": "maturations.valid_from",
            "valid_to": "maturations.valid_to",
        }
        unexpected = set(action.parameters).difference(allowed)
        if unexpected:
            raise DicValidationError(f"unsupported maturation fields: {sorted(unexpected)}")
        category = _parameter_text(action.parameters, "category", required=True)
        await self.click("maturations.new")
        for parameter, selector_key in allowed.items():
            value = _parameter_text(action.parameters, parameter)
            if value is not None:
                await self.fill(selector_key, value)
        await self.click("maturations.save")
        await self.confirm_if_present(
            expected_identity=(
                action.employee_id,
                category or "",
            )
        )


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

    async def read_correction_state(
        self, employee_id: str, year: int, month: int, category: str
    ) -> BalanceCorrectionState:
        if isinstance(year, bool) or year not in range(2000, 2201):
            raise DicValidationError("year must be between 2000 and 2200")
        if isinstance(month, bool) or month not in range(1, 13):
            raise DicValidationError("month must be between 1 and 12")
        normalized_category = category.strip().casefold()
        if not normalized_category:
            raise DicValidationError("category is required")
        await self.open(employee_id)
        await self.select("balance.year", str(year))
        await self.select("balance.month", str(month))
        rows = await self.all_matches("balance.rows")
        matches: list[LocatorLike] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed = (await self.read_text("balance_row.category", root=row) or "").casefold()
            if observed == normalized_category:
                matches.append(row)
        if len(matches) != 1:
            raise DicValidationError("balance category must identify exactly one row")
        raw_current = await self.read_text("balance_row.corrections", root=matches[0])
        try:
            current = canonical_decimal_text(raw_current)
        except ValueError as exc:
            raise DicValidationError(
                "current balance correction is not a canonical decimal"
            ) from exc
        return BalanceCorrectionState(
            employee_id=employee_id,
            year=year,
            month=month,
            category=category.strip(),
            current_value=current,
        )

    async def execute(self, action: PreparedAction) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        expected_keys = {"year", "month", "category", "previous_value", "amount"}
        if set(action.parameters) != expected_keys:
            raise DicValidationError("balance correction parameters are incomplete or unsupported")
        year = action.parameters.get("year")
        month = action.parameters.get("month")
        if not isinstance(year, int) or isinstance(year, bool):
            raise DicValidationError("year must be an integer")
        if not isinstance(month, int) or isinstance(month, bool):
            raise DicValidationError("month must be an integer")
        category = _parameter_text(action.parameters, "category", required=True)
        previous_value = _parameter_text(action.parameters, "previous_value", required=True)
        amount = _parameter_text(action.parameters, "amount", required=True)
        try:
            expected_previous = canonical_decimal_text(previous_value)
            canonical_amount = canonical_decimal_text(amount)
        except ValueError as exc:
            raise DicValidationError("balance values must be canonical decimals") from exc
        if canonical_amount == expected_previous:
            raise DicValidationError("balance correction would not change state")
        state = await self.read_correction_state(action.employee_id, year, month, category or "")
        if state.current_value != expected_previous:
            raise DicValidationError("balance correction precondition changed")
        await self.click("balance.correct")
        await self.select("balance.correction_month", str(month))
        await self.fill("balance.category", category or "")
        await self.fill("balance.amount", canonical_amount)
        await self.click("balance.save")
        await self.confirm_if_present(
            expected_identity=(action.employee_id, str(year), str(month), category or "")
        )


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

    async def _rows(self) -> LocatorLike:
        if await self.locate("documents.container", required=False) is None:
            raise DicUiChangedError("document list container is unavailable")
        return await self.all_matches("documents.rows")

    async def _find_row(self, document_id: str) -> LocatorLike:
        if _is_fallback_record_id(document_id, "DOC"):
            raise DicValidationError("fallback document identifiers are not actionable")
        rows = await self._rows()
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
        rows = await self._rows()
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
            stable_identifier = bool(record_id)
            edit = await self.locate("documents.edit", root=row, required=False)
            delete = await self.locate("documents.delete", root=row, required=False)
            result.append(
                DocumentMetadata(
                    document_id=record_id
                    or _stable_record_id("DOC", category, expiry_date, uploaded_at, str(index)),
                    employee_id=employee_id,
                    stable_identifier=stable_identifier,
                    actionable=stable_identifier and edit is not None and delete is not None,
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

    async def stable_document_ids(self, employee_id: str) -> frozenset[str]:
        """Return only stable DOM identifiers for an in-process upload baseline."""

        await self.open(employee_id)
        rows = await self._rows()
        identifiers: set[str] = set()
        for index in range(await rows.count()):
            value = await self.read_attribute(
                "document_row.id", "data-document-id", root=rows.nth(index)
            )
            if value:
                identifiers.add(value)
        return frozenset(identifiers)

    async def verify_uploaded_document(
        self,
        employee_id: str,
        baseline_ids: frozenset[str],
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Verify one new DOM-stable document and its raw expected metadata."""

        await self.open(employee_id)
        rows = await self._rows()
        expected = {
            key: value
            for key, value in parameters.items()
            if key in {"category", "expiry_date"} and isinstance(value, str)
        }
        if not expected:
            return None
        candidates: list[tuple[str, dict[str, str | None]]] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            document_id = await self.read_attribute("document_row.id", "data-document-id", root=row)
            if not document_id or document_id in baseline_ids:
                continue
            candidates.append(
                (
                    document_id,
                    {
                        "title": await self.read_text("document_row.title", root=row),
                        "category": await self.read_text("document_row.category", root=row),
                        "expiry_date": await self.read_text("document_row.expiry", root=row),
                    },
                )
            )
        if len(candidates) != 1:
            return None
        _document_id, observed = candidates[0]
        return all(_expected_value_matches(observed[key], value) for key, value in expected.items())

    async def verify_expected_metadata(
        self,
        employee_id: str,
        document_id: str,
        parameters: dict[str, JsonValue],
    ) -> bool | None:
        """Compare editable raw metadata for one DOM-stable document."""

        if _is_fallback_record_id(document_id, "DOC"):
            return None
        await self.open(employee_id)
        expected = {
            key: value
            for key, value in parameters.items()
            if key in {"category", "expiry_date"} and isinstance(value, str)
        }
        if not expected:
            return None
        rows = await self._rows()
        matches = 0
        for index in range(await rows.count()):
            row = rows.nth(index)
            observed_id = await self.read_attribute("document_row.id", "data-document-id", root=row)
            if observed_id != document_id:
                continue
            observed = {
                "category": await self.read_text("document_row.category", root=row),
                "expiry_date": await self.read_text("document_row.expiry", root=row),
            }
            if all(
                _expected_value_matches(observed[key], value) for key, value in expected.items()
            ):
                matches += 1
        return matches == 1

    async def execute(
        self, action: PreparedAction, *, verified_upload: VerifiedUploadPayload | None = None
    ) -> None:
        if action.employee_id is None:
            raise DicValidationError("employee_id is required")
        await self.open(action.employee_id)
        confirmation_identity: tuple[str, ...]
        if action.function_id is FunctionId.EMP_DOC_002:
            if verified_upload is None:
                raise DicValidationError("document upload requires a verified adapter payload")
            if set(action.parameters).difference({"category", "expiry_date"}):
                raise DicValidationError("document upload contains unsupported metadata")
            category = _parameter_text(action.parameters, "category", required=True)
            expiry = _parameter_text(action.parameters, "expiry_date")
            await self.click("documents.upload")
            file_input = await self.locate("documents.file")
            if file_input is None:
                raise DicUiChangedError("document file input is unavailable")
            await file_input.set_input_files(verified_upload.as_playwright())
            if category:
                await self.fill("documents.category", category)
            if expiry:
                await self.fill("documents.expiry", expiry)
            await self.click("documents.save")
            confirmation_identity = (action.employee_id, category or "")
        elif action.function_id is FunctionId.EMP_DOC_004:
            if set(action.parameters).difference({"document_id", "category", "expiry_date"}):
                raise DicValidationError("document update contains unsupported metadata")
            if not ({"category", "expiry_date"} & set(action.parameters)):
                raise DicValidationError("document update requires category or expiry_date")
            document_id = _parameter_text(action.parameters, "document_id", required=True)
            await self.click("documents.edit", root=await self._find_row(document_id or ""))
            category = _parameter_text(action.parameters, "category")
            expiry = _parameter_text(action.parameters, "expiry_date")
            if category:
                await self.fill("documents.category", category)
            if expiry:
                await self.fill("documents.expiry", expiry)
            await self.click("documents.save")
            confirmation_identity = (action.employee_id, document_id or "")
        elif action.function_id is FunctionId.EMP_DOC_005:
            if set(action.parameters) != {"document_id"}:
                raise DicValidationError("document delete requires only document_id")
            document_id = _parameter_text(action.parameters, "document_id", required=True)
            await self.click("documents.delete", root=await self._find_row(document_id or ""))
            confirmation_identity = (action.employee_id, document_id or "")
        else:
            raise DicValidationError("function is not handled by documents page")
        await self.confirm_if_present(expected_identity=confirmation_identity)
