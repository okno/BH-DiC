"""Page Objects for every supported route under the DIC Employees menu."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urlsplit

from pydantic import JsonValue

from bh_dic.dic.employee_list_capture import (
    EMPLOYEE_LIST_PAGE_SIZE,
    EmployeeListResponseCapture,
    employee_list_result_from_response,
)
from bh_dic.dic.employee_list_capture import (
    ResponseLike as EmployeeResponseLike,
)
from bh_dic.dic.employee_resource_capture import (
    CONTRACTS_ENDPOINT,
    DOCUMENTS_ENDPOINT,
    MATURATIONS_ENDPOINT,
    contracts_from_items,
    documents_from_items,
    maturations_from_items,
)
from bh_dic.dic.errors import (
    DicConfigurationError,
    DicNotFoundError,
    DicUiChangedError,
    DicValidationError,
)
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
from bh_dic.dic.pages.base import BaseDicPage, LocatorLike, PageLike, VerifiedUploadPayload
from bh_dic.dic.paginated_capture import (
    PaginatedResponseCapture,
    collect_complete_pages,
    page_from_response,
)
from bh_dic.dic.payroll_capture import PayrollResponseCapture, payrolls_from_response
from bh_dic.dic.selectors import DEFAULT_SELECTORS, SelectorRegistry
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

    _HYDRATION_POLL_SECONDS = 0.05

    def __init__(
        self,
        page: PageLike,
        base_url: str,
        *,
        selectors: SelectorRegistry = DEFAULT_SELECTORS,
        timeout_ms: float = 15_000,
        expected_tenant_id: str | None = None,
    ) -> None:
        if (
            expected_tenant_id is not None
            and re.fullmatch(r"[1-9][0-9]{0,18}", expected_tenant_id) is None
        ):
            raise DicConfigurationError("expected employee tenant has an invalid format")
        super().__init__(page, base_url, selectors=selectors, timeout_ms=timeout_ms)
        self.expected_tenant_id = expected_tenant_id

    def _assert_exact_route(self) -> None:
        parsed = None
        port: int | None = None
        route_parse_failed = False
        try:
            parsed = urlsplit(self.page.url)
            port = parsed.port
        except (TypeError, ValueError):
            route_parse_failed = True
        if route_parse_failed or parsed is None:
            raise DicUiChangedError("employee list reached an invalid route")
        if (
            parsed.scheme != "https"
            or parsed.netloc != "secure.dipendentincloud.it"
            or parsed.hostname != "secure.dipendentincloud.it"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.path != self.route_template
            or parsed.query
            or parsed.fragment
        ):
            raise DicUiChangedError("employee list reached an unexpected route")

    async def _strict_control(self, key: str) -> LocatorLike:
        candidates = self.selectors.candidates(key)
        if len(candidates) != 1:
            raise DicUiChangedError("employee UI control registry is ambiguous")
        locator = self._candidate_locator(self.page, candidates[0])
        count: int | None = None
        count_failed = False
        try:
            count = await locator.count()
        except Exception:
            count_failed = True
        if count_failed or count != 1:
            raise DicUiChangedError("employee UI control is unavailable")
        return locator.first

    async def _visible_candidates(self, key: str) -> tuple[LocatorLike, ...]:
        visible: list[LocatorLike] = []
        for candidate in self.selectors.candidates(key):
            locator = self._candidate_locator(self.page, candidate)
            count: int | None = None
            count_failed = False
            try:
                count = await locator.count()
            except Exception:
                count_failed = True
            if count_failed or count is None:
                raise DicUiChangedError("consent control state is unavailable")
            for index in range(count):
                item = locator.nth(index)
                item_visible = False
                visibility_failed = False
                try:
                    item_visible = await item.is_visible()
                except Exception:
                    visibility_failed = True
                if visibility_failed:
                    raise DicUiChangedError("consent control state is unavailable")
                if item_visible:
                    visible.append(item)
        return tuple(visible)

    async def _first_visible_candidate(self, key: str) -> LocatorLike | None:
        """Resolve ordered alternative consent selectors without double-counting one node."""

        for candidate in self.selectors.candidates(key):
            locator = self._candidate_locator(self.page, candidate)
            count: int | None = None
            count_failed = False
            try:
                count = await locator.count()
            except Exception:
                count_failed = True
            if count_failed or count is None:
                raise DicUiChangedError("consent control state is unavailable")
            if count > 1:
                raise DicUiChangedError("non-essential consent controls are ambiguous")
            if count == 1:
                item_visible = False
                visibility_failed = False
                try:
                    item_visible = await locator.first.is_visible()
                except Exception:
                    visibility_failed = True
                if visibility_failed:
                    raise DicUiChangedError("consent control state is unavailable")
                if item_visible:
                    return locator.first
        return None

    async def _reject_nonessential_consent(self) -> None:
        self._assert_exact_route()
        banners = await self._visible_candidates("consent.onetrust_banner")
        reject = await self._first_visible_candidate("consent.reject_nonessential")
        if banners and reject is None:
            raise DicUiChangedError("non-essential consent cannot be rejected")
        if reject is None:
            return
        click_failed = False
        try:
            await reject.click()
        except Exception:
            click_failed = True
        if click_failed:
            raise DicUiChangedError("non-essential consent rejection failed")
        self._assert_exact_route()
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        while True:
            if not await self._visible_candidates(
                "consent.onetrust_banner"
            ) and not await self._visible_candidates("consent.reject_nonessential"):
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DicUiChangedError("non-essential consent rejection could not be verified")
            await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))
            self._assert_exact_route()

    async def _wait_for_hydration(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        keys = (
            "employees.filter.active",
            "employees.filter.inactive",
            "employees.filter.all",
        )
        while True:
            self._assert_exact_route()
            await self._reject_nonessential_consent()
            ready = True
            for key in keys:
                try:
                    control = await self._strict_control(key)
                    ready = ready and await control.is_visible()
                except Exception:
                    ready = False
            if ready:
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DicUiChangedError("employee list did not hydrate")
            await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))

    async def _reset_response_generation(self) -> None:
        """Drain the previous document before installing the employee-response listener."""

        reset_failed = False
        try:
            await self.page.goto(
                "about:blank",
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            await self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0)
        except Exception:
            reset_failed = True
        if reset_failed:
            raise DicUiChangedError("employee response generation reset failed")
        if self.page.url != "about:blank":
            raise DicUiChangedError(
                "employee response generation reset reached an unexpected route"
            )

    @staticmethod
    def _query_state(
        requested: EmployeeListQuery,
        *,
        search: str | None,
        employee_filter: EmployeeFilter,
        sort_by: Literal["name", "payroll_number", "status", "contract"],
        sort_direction: SortDirection,
        page: int,
    ) -> EmployeeListQuery:
        return requested.model_copy(
            update={
                "query": search,
                "employee_filter": employee_filter,
                "sort_by": sort_by,
                "sort_direction": sort_direction,
                "page": page,
                "page_size": EMPLOYEE_LIST_PAGE_SIZE,
            }
        )

    async def _validated_result(
        self,
        response: EmployeeResponseLike,
        expected: EmployeeListQuery,
    ) -> EmployeeListResult:
        self._assert_exact_route()
        result = await employee_list_result_from_response(
            response,
            expected,
            expected_tenant_id=self.expected_tenant_id,
        )
        await asyncio.sleep(0)
        self._assert_exact_route()
        return result

    async def _action_result(
        self,
        capture: EmployeeListResponseCapture,
        expected: EmployeeListQuery,
        action: Callable[[], Awaitable[object]],
    ) -> EmployeeListResult:
        await self._reject_nonessential_consent()
        mark = capture.mark()
        action_failed = False
        try:
            await action()
        except DicUiChangedError:
            raise
        except Exception:
            action_failed = True
        if action_failed:
            raise DicUiChangedError("employee UI action failed")
        self._assert_exact_route()
        response = await capture.wait_for(
            expected,
            after_sequence=mark,
            timeout_ms=self.timeout_ms,
        )
        return await self._validated_result(response, expected)

    async def _click_strict_control(self, key: str) -> None:
        control = await self._strict_control(key)
        await control.click()

    async def _wait_sort_state(self, control: LocatorLike, expected: str) -> str:
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        while True:
            self._assert_exact_route()
            observed: str | None = None
            state_read_failed = False
            try:
                observed = (await control.get_attribute("aria-sort") or "none").casefold()
            except Exception:
                state_read_failed = True
            if state_read_failed or observed is None:
                raise DicUiChangedError("employee sort state cannot be verified")
            if observed == expected:
                return observed
            if observed not in {"none", "ascending", "descending"}:
                raise DicUiChangedError("employee sort state cannot be verified")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DicUiChangedError("employee sort state did not settle")
            await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))

    async def _wait_next_enabled(self) -> LocatorLike:
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        while True:
            self._assert_exact_route()
            next_button = await self._strict_control("employees.next")
            disabled: str | None = None
            aria_disabled: str | None = None
            state_read_failed = False
            try:
                disabled = await next_button.get_attribute("disabled")
                aria_disabled = (await next_button.get_attribute("aria-disabled") or "").casefold()
            except Exception:
                state_read_failed = True
            if state_read_failed or aria_disabled is None:
                raise DicUiChangedError("employee pagination state is unavailable")
            if disabled is None and aria_disabled != "true":
                return next_button
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DicUiChangedError("employee pagination did not become available")
            await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))

    async def _sort_result(
        self,
        capture: EmployeeListResponseCapture,
        requested: EmployeeListQuery,
        current: EmployeeListQuery,
        result: EmployeeListResult,
    ) -> tuple[EmployeeListQuery, EmployeeListResult]:
        target = "ascending" if requested.sort_direction is SortDirection.ASC else "descending"
        if requested.sort_by == "name" and target == "ascending":
            control = await self._strict_control("employees.sort.name")
            await self._wait_sort_state(control, target)
            return current, result
        if requested.sort_by not in {"name", "contract"}:
            raise DicUiChangedError("requested employee sort is not live-verified")

        control = await self._strict_control(f"employees.sort.{requested.sort_by}")
        initial_sort_state = (
            "ascending"
            if current.sort_by == requested.sort_by and current.sort_direction is SortDirection.ASC
            else "descending"
            if current.sort_by == requested.sort_by
            else "none"
        )
        aria_sort = await self._wait_sort_state(control, initial_sort_state)
        for _ in range(2):
            if aria_sort == target:
                break
            next_sort = "ascending" if aria_sort in {"none", "descending"} else "descending"
            mark = capture.mark()
            await self._reject_nonessential_consent()
            click_failed = False
            try:
                await control.click()
            except Exception:
                click_failed = True
            if click_failed:
                raise DicUiChangedError("employee sort action failed")
            self._assert_exact_route()
            current = self._query_state(
                requested,
                search=current.query,
                employee_filter=current.employee_filter,
                sort_by=requested.sort_by,
                sort_direction=(
                    SortDirection.ASC if next_sort == "ascending" else SortDirection.DESC
                ),
                page=1,
            )
            response = await capture.wait_for(
                current,
                after_sequence=mark,
                timeout_ms=self.timeout_ms,
            )
            result = await self._validated_result(response, current)
            aria_sort = await self._wait_sort_state(control, next_sort)
        if aria_sort != target:
            raise DicUiChangedError("employee sort did not reach the requested state")
        return current, result

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
            payroll_number=self.redact_tail(await self.read_text("row.payroll_number", root=row)),
            contract_label=await self.read_text("row.contract", root=row),
            contract_state=await self.read_text("row.contract_state", root=row),
            contract_period=await self.read_text("row.contract_period", root=row),
            schedule_model=await self.read_text("row.schedule", root=row),
            workplace=await self.read_text("row.workplace", root=row),
            account_state=_account_state(await self.read_text("row.account_state", root=row)),
            employee_state=_employee_state(await self.read_text("row.employee_state", root=row)),
        )

    async def list(self, query: EmployeeListQuery) -> EmployeeListResult:
        if query.sort_by not in {"name", "contract"}:
            raise DicUiChangedError("requested employee sort is not live-verified")
        await self._reset_response_generation()
        initial = self._query_state(
            query,
            search=None,
            employee_filter=EmployeeFilter.ACTIVE,
            sort_by="name",
            sort_direction=SortDirection.ASC,
            page=1,
        )
        with EmployeeListResponseCapture(self.page) as capture:
            navigation_mark = capture.mark()
            await self.navigate()
            self._assert_exact_route()
            await self._reject_nonessential_consent()
            await self._wait_for_hydration()
            response = await capture.wait_for(
                initial,
                after_sequence=navigation_mark,
                timeout_ms=self.timeout_ms,
            )
            current = initial
            result = await self._validated_result(response, current)

            if query.query:
                current = self._query_state(
                    query,
                    search=query.query,
                    employee_filter=current.employee_filter,
                    sort_by=current.sort_by,
                    sort_direction=current.sort_direction,
                    page=1,
                )
                result = await self._action_result(
                    capture,
                    current,
                    lambda: self.fill("employees.search", query.query or ""),
                )

            if query.employee_filter is not EmployeeFilter.ACTIVE:
                current = self._query_state(
                    query,
                    search=current.query,
                    employee_filter=query.employee_filter,
                    sort_by=current.sort_by,
                    sort_direction=current.sort_direction,
                    page=1,
                )
                result = await self._action_result(
                    capture,
                    current,
                    lambda: self._click_strict_control(
                        f"employees.filter.{query.employee_filter.value}"
                    ),
                )

            current, result = await self._sort_result(capture, query, current, result)

            for page_number in range(2, query.page + 1):
                if not result.has_next:
                    raise DicUiChangedError("employee pagination ended before the requested page")
                next_button = await self._wait_next_enabled()
                current = self._query_state(
                    query,
                    search=current.query,
                    employee_filter=current.employee_filter,
                    sort_by=current.sort_by,
                    sort_direction=current.sort_direction,
                    page=page_number,
                )
                result = await self._action_result(
                    capture,
                    current,
                    next_button.click,
                )

            return result

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
        role_state_unavailable = False
        try:
            current = await role.is_checked()
        except Exception:
            aria_checked: str | None = None
            try:
                aria_checked = await role.get_attribute("aria-checked")
            except Exception:
                role_state_unavailable = True
            if aria_checked in {"true", "false"}:
                current = aria_checked == "true"
            else:
                current = False
                role_state_unavailable = True
        if role_state_unavailable:
            raise DicUiChangedError("role control state is unavailable")
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
        with PaginatedResponseCapture(self.page, CONTRACTS_ENDPOINT) as capture:
            mark = capture.mark()
            await self.open(employee_id)
            response = await capture.wait_for(
                employee_id,
                after_sequence=mark,
                timeout_ms=self.timeout_ms,
            )
            first = await page_from_response(
                response,
                contract=CONTRACTS_ENDPOINT,
                employee_id=employee_id,
            )
            items = await collect_complete_pages(
                self.page,
                first,
                contract=CONTRACTS_ENDPOINT,
                employee_id=employee_id,
            )
        return contracts_from_items(items, employee_id=employee_id)

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
        with PaginatedResponseCapture(self.page, MATURATIONS_ENDPOINT) as capture:
            mark = capture.mark()
            await self.open(employee_id)
            response = await capture.wait_for(
                employee_id,
                after_sequence=mark,
                timeout_ms=self.timeout_ms,
            )
            first = await page_from_response(
                response,
                contract=MATURATIONS_ENDPOINT,
                employee_id=employee_id,
            )
            items = await collect_complete_pages(
                self.page,
                first,
                contract=MATURATIONS_ENDPOINT,
                employee_id=employee_id,
            )
        return maturations_from_items(items, employee_id=employee_id)

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

    _HYDRATION_POLL_SECONDS = 0.05

    async def _wait_for_year_control(self) -> tuple[Literal["custom", "legacy"], int | None]:
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        while True:
            custom = await self.locate("payrolls.year_selector", required=False)
            if custom is not None:
                current_text = await self.read_text("payrolls.year_current")
                if current_text is not None and current_text.isdigit():
                    current = int(current_text)
                    if 2000 <= current <= 2200:
                        return "custom", current
            legacy = await self.locate("payrolls.year", required=False)
            if legacy is not None:
                return "legacy", None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DicUiChangedError("payroll page did not hydrate")
            await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))

    async def _select_custom_year(self, current: int, expected: int) -> None:
        if abs(expected - current) > 50:
            raise DicValidationError("requested payroll year is outside the bounded UI range")
        while current != expected:
            key = "payrolls.year_next" if expected > current else "payrolls.year_previous"
            await self.click(key)
            wanted = current + (1 if expected > current else -1)
            deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
            while True:
                observed = await self.read_text("payrolls.year_current")
                if observed == str(wanted):
                    current = wanted
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise DicUiChangedError("payroll year selection did not settle")
                await asyncio.sleep(min(self._HYDRATION_POLL_SECONDS, remaining))

    async def _read_legacy(
        self,
        employee_id: str,
        year: int | None,
    ) -> tuple[PayrollMetadata, ...]:
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

    async def read(self, employee_id: str, year: int | None = None) -> tuple[PayrollMetadata, ...]:
        with PayrollResponseCapture(self.page) as capture:
            navigation_mark = capture.mark()
            await self.open(employee_id)
            control_kind, current_year = await self._wait_for_year_control()
            if control_kind == "legacy":
                return await self._read_legacy(employee_id, year)
            if current_year is None:
                raise DicUiChangedError("payroll year is unavailable")
            expected_year = current_year if year is None else year
            response_mark = navigation_mark
            if expected_year != current_year:
                response_mark = capture.mark()
                await self._select_custom_year(current_year, expected_year)
            response = await capture.wait_for(
                employee_id,
                expected_year,
                after_sequence=response_mark,
                timeout_ms=self.timeout_ms,
            )
            return await payrolls_from_response(
                response,
                employee_id=employee_id,
                year=expected_year,
            )


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
        with PaginatedResponseCapture(self.page, DOCUMENTS_ENDPOINT) as capture:
            mark = capture.mark()
            await self.open(employee_id)
            response = await capture.wait_for(
                employee_id,
                after_sequence=mark,
                timeout_ms=self.timeout_ms,
            )
            first = await page_from_response(
                response,
                contract=DOCUMENTS_ENDPOINT,
                employee_id=employee_id,
            )
            items = await collect_complete_pages(
                self.page,
                first,
                contract=DOCUMENTS_ENDPOINT,
                employee_id=employee_id,
            )
        return documents_from_items(items, employee_id=employee_id, query=query)

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
