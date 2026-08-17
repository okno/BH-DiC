from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from pydantic import JsonValue

from bh_dic.dic.employee_list_capture import (
    EMPLOYEE_LIST_ENDPOINT_ORIGIN,
    EMPLOYEE_LIST_ENDPOINT_PATH,
)
from bh_dic.dic.errors import DicNotFoundError, DicUiChangedError, DicValidationError
from bh_dic.dic.models import (
    AccountState,
    DocumentQuery,
    EmployeeFilter,
    EmployeeListQuery,
    EmployeeState,
    FunctionId,
    PreparedAction,
    SortDirection,
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
    TimestampEmployeesPage,
    VerifiedUploadPayload,
)
from bh_dic.dic.selectors import DEFAULT_SELECTORS, SelectorKind

SelectorSignature = tuple[SelectorKind, str, str | None, bool]


def _selector_index() -> dict[SelectorSignature, tuple[str, ...]]:
    mutable: dict[SelectorSignature, list[str]] = {}
    for key in DEFAULT_SELECTORS.keys:
        for candidate in DEFAULT_SELECTORS.candidates(key):
            signature = (candidate.kind, candidate.value, candidate.name, candidate.exact)
            mutable.setdefault(signature, []).append(key)
    return {signature: tuple(keys) for signature, keys in mutable.items()}


SELECTOR_INDEX = _selector_index()


class SyntheticNode:
    def __init__(
        self,
        *,
        text: str = "",
        value: str | None = None,
        attributes: dict[str, str] | None = None,
        checked: bool = False,
        visible: bool = True,
        checked_error: bool = False,
        on_click: Callable[[SyntheticNode], None] | None = None,
        on_fill: Callable[[SyntheticNode, str], None] | None = None,
    ) -> None:
        self.text = text
        self.value = value
        self.attributes = attributes or {}
        self.checked = checked
        self.visible = visible
        self.checked_error = checked_error
        self.on_click = on_click
        self.on_fill = on_fill
        self.children: dict[str, list[SyntheticNode]] = {}
        self.clicks = 0
        self.filled: list[str] = []
        self.selected: list[str] = []
        self.uploaded_files: list[object] = []

    def add(self, key: str, *nodes: SyntheticNode) -> None:
        self.children.setdefault(key, []).extend(nodes)


class SyntheticLocator:
    def __init__(self, nodes: Sequence[SyntheticNode]) -> None:
        self.nodes = list(nodes)

    @property
    def first(self) -> Self:
        return type(self)(self.nodes[:1])

    def nth(self, index: int) -> Self:
        return type(self)(self.nodes[index : index + 1])

    def _lookup(self, signature: SelectorSignature) -> Self:
        matches: list[SyntheticNode] = []
        for node in self.nodes:
            for key in SELECTOR_INDEX.get(signature, ()):
                matches.extend(node.children.get(key, ()))
        return type(self)(matches)

    def locator(self, selector: str) -> Self:
        return self._lookup((SelectorKind.CSS, selector, None, False))

    def get_by_role(self, role: str, *, name: str | None = None, exact: bool | None = None) -> Self:
        return self._lookup((SelectorKind.ROLE, role, name, bool(exact)))

    def get_by_label(self, text: str, *, exact: bool | None = None) -> Self:
        return self._lookup((SelectorKind.LABEL, text, None, bool(exact)))

    def get_by_placeholder(self, text: str) -> Self:
        return self._lookup((SelectorKind.PLACEHOLDER, text, None, False))

    def get_by_test_id(self, test_id: str) -> Self:
        return self._lookup((SelectorKind.TEST_ID, test_id, None, False))

    def get_by_text(self, text: str, *, exact: bool | None = None) -> Self:
        return self._lookup((SelectorKind.TEXT, text, None, bool(exact)))

    async def count(self) -> int:
        return len(self.nodes)

    def _one(self) -> SyntheticNode:
        if not self.nodes:
            raise AssertionError("synthetic locator is empty")
        return self.nodes[0]

    async def inner_text(self) -> str:
        return self._one().text

    async def evaluate(self, expression: str) -> object:
        del expression

        def snapshot(node: SyntheticNode) -> dict[str, object]:
            return {
                "attributes": dict(sorted(node.attributes.items())),
                "checked": node.checked,
                "children": {
                    key: [snapshot(child) for child in children]
                    for key, children in sorted(node.children.items())
                },
                "text": node.text,
                "value": node.value,
            }

        return [snapshot(node) for node in self.nodes]

    async def get_attribute(self, name: str) -> str | None:
        return self._one().attributes.get(name)

    async def input_value(self) -> str:
        node = self._one()
        if node.value is None:
            raise RuntimeError("synthetic node is not an input")
        return node.value

    async def is_checked(self) -> bool:
        node = self._one()
        if node.checked_error:
            raise RuntimeError("synthetic checkbox does not expose native state")
        return node.checked

    async def is_visible(self) -> bool:
        return self._one().visible

    async def click(self) -> None:
        node = self._one()
        node.clicks += 1
        if node.on_click is not None:
            node.on_click(node)

    async def fill(self, value: str) -> None:
        node = self._one()
        node.value = value
        node.filled.append(value)
        if node.on_fill is not None:
            node.on_fill(node, value)

    async def select_option(self, value: str) -> None:
        node = self._one()
        node.value = value
        node.selected.append(value)

    async def set_checked(self, checked: bool) -> None:
        self._one().checked = checked

    async def set_input_files(self, files: str | Sequence[str] | dict[str, object]) -> None:
        node = self._one()
        if isinstance(files, (str, dict)):
            node.uploaded_files.append(files)
        else:
            node.uploaded_files.extend(files)


class SyntheticPage:
    def __init__(self) -> None:
        self.root = SyntheticNode()
        self._root_locator = SyntheticLocator([self.root])
        self._url = ""
        self.visited: list[str] = []
        self.response_handlers: list[object] = []

    def add(self, key: str, *nodes: SyntheticNode) -> None:
        container_key = {
            "employees.rows": "employees.container",
            "contracts.rows": "contracts.container",
            "maturations.rows": "maturations.container",
            "documents.rows": "documents.container",
        }.get(key)
        if container_key is not None and container_key not in self.root.children:
            self.root.add(container_key, SyntheticNode())
        self.root.add(key, *nodes)

    @property
    def url(self) -> str:
        return self._url

    @property
    def first(self) -> SyntheticLocator:
        return self._root_locator.first

    def nth(self, index: int) -> SyntheticLocator:
        return self._root_locator.nth(index)

    def locator(self, selector: str) -> SyntheticLocator:
        if selector == "body":
            return self._root_locator
        return self._root_locator.locator(selector)

    def get_by_role(
        self, role: str, *, name: str | None = None, exact: bool | None = None
    ) -> SyntheticLocator:
        return self._root_locator.get_by_role(role, name=name, exact=exact)

    def get_by_label(self, text: str, *, exact: bool | None = None) -> SyntheticLocator:
        return self._root_locator.get_by_label(text, exact=exact)

    def get_by_placeholder(self, text: str) -> SyntheticLocator:
        return self._root_locator.get_by_placeholder(text)

    def get_by_test_id(self, test_id: str) -> SyntheticLocator:
        return self._root_locator.get_by_test_id(test_id)

    def get_by_text(self, text: str, *, exact: bool | None = None) -> SyntheticLocator:
        return self._root_locator.get_by_text(text, exact=exact)

    async def count(self) -> int:
        return 1

    async def inner_text(self) -> str:
        return ""

    async def get_attribute(self, name: str) -> str | None:
        return self.root.attributes.get(name)

    async def input_value(self) -> str:
        raise RuntimeError("synthetic page is not an input")

    async def is_checked(self) -> bool:
        return False

    async def is_visible(self) -> bool:
        return True

    async def click(self) -> None:
        return None

    async def fill(self, value: str) -> None:
        del value

    async def select_option(self, value: str) -> None:
        del value

    async def set_checked(self, checked: bool) -> None:
        del checked

    async def set_input_files(self, files: str | Sequence[str]) -> None:
        del files

    async def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> object:
        del wait_until, timeout
        self._url = url
        self.visited.append(url)
        return object()

    async def wait_for_load_state(
        self,
        state: str | None = None,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> None:
        del state, timeout

    def on(self, event: str, handler: object) -> None:
        assert event == "response"
        self.response_handlers.append(handler)

    def remove_listener(self, event: str, handler: object) -> None:
        assert event == "response"
        self.response_handlers.remove(handler)

    def emit_response(self, response: object) -> None:
        for handler in tuple(self.response_handlers):
            handler(response)  # type: ignore[operator]


@dataclass(frozen=True)
class SyntheticRequest:
    method: str = "GET"


class SyntheticResponse:
    def __init__(
        self,
        url: str,
        document: dict[str, Any],
        *,
        on_body: Callable[[], None] | None = None,
    ) -> None:
        self.url = url
        self.status = 200
        self.request = SyntheticRequest()
        self._body = json.dumps(document).encode()
        self._on_body = on_body

    async def body(self) -> bytes:
        if self._on_body is not None:
            callback, self._on_body = self._on_body, None
            callback()
        return self._body

    async def header_value(self, name: str) -> str | None:
        return {
            "content-type": "application/json",
            "content-length": str(len(self._body)),
        }.get(name.casefold())


def _api_employee(
    employee_id: int,
    *,
    name: str = "Alice Example",
    active: bool = True,
    job_title: str = "Tester",
) -> dict[str, Any]:
    return {
        "id": employee_id,
        "company_id": 123_456_789,
        "active": active,
        "full_name": name,
        "email": "synthetic@example.invalid",
        "tax_code": "SYNTHETIC123456",
        "number": "M-0001",
        "job_title": job_title,
        "has_access": True,
        "invited": False,
        "current_contract": {
            "id": employee_id + 1_000,
            "hours_type": "Full time",
            "part_time_percentage": 100,
            "permanent": False,
            "valid_from": "2026-01-01",
            "valid_to": "2026-09-30",
        },
        "current_workplace": {"id": 1, "name": "Sede sintetica"},
        "main_role": {
            "role": {"id": 1, "name": "Employee", "category": "worker"},
            "team": {"id": 2, "name": "Synthetic"},
        },
    }


class EmployeeApiSyntheticPage(SyntheticPage):
    """Small UI fake: only UI actions emit the passively observed API response."""

    def __init__(self) -> None:
        super().__init__()
        self.api_pages: dict[int, list[dict[str, Any]]] = {1: []}
        self.api_total = 0
        self.search = ""
        self.employee_filter = EmployeeFilter.ACTIVE
        self.sort_by = "name"
        self.sort_direction = SortDirection.ASC
        self.page_number = 1

        self.search_node = SyntheticNode(value="", on_fill=self._search_filled)
        self.active_filter = SyntheticNode(
            on_click=lambda _node: self._set_filter(EmployeeFilter.ACTIVE)
        )
        self.inactive_filter = SyntheticNode(
            on_click=lambda _node: self._set_filter(EmployeeFilter.INACTIVE)
        )
        self.all_filter = SyntheticNode(on_click=lambda _node: self._set_filter(EmployeeFilter.ALL))
        self.name_sort = SyntheticNode(
            attributes={"aria-sort": "ascending"},
            on_click=lambda node: self._set_sort("name", node),
        )
        self.contract_sort = SyntheticNode(
            attributes={"aria-sort": "none"},
            on_click=lambda node: self._set_sort("contract", node),
        )
        self.next_button = SyntheticNode(on_click=lambda _node: self._next_page())
        self.add("employees.search", self.search_node)
        self.add("employees.filter.active", self.active_filter)
        self.add("employees.filter.inactive", self.inactive_filter)
        self.add("employees.filter.all", self.all_filter)
        self.add("employees.sort.name", self.name_sort)
        self.add("employees.sort.contract", self.contract_sort)
        self.add("employees.next", self.next_button)

    def _reset_state(self) -> None:
        self.search = ""
        self.employee_filter = EmployeeFilter.ACTIVE
        self.sort_by = "name"
        self.sort_direction = SortDirection.ASC
        self.page_number = 1
        self.search_node.value = ""
        self.name_sort.attributes["aria-sort"] = "ascending"
        self.contract_sort.attributes["aria-sort"] = "none"

    def _search_filled(self, _node: SyntheticNode, value: str) -> None:
        self.search = value
        self.page_number = 1
        self._emit_employee_response()

    def _set_filter(self, employee_filter: EmployeeFilter) -> None:
        self.employee_filter = employee_filter
        self.page_number = 1
        self._emit_employee_response()

    def _set_sort(self, sort_by: str, node: SyntheticNode) -> None:
        current = node.attributes.get("aria-sort", "none")
        next_state = "ascending" if current in {"none", "descending"} else "descending"
        self.name_sort.attributes["aria-sort"] = "none"
        self.contract_sort.attributes["aria-sort"] = "none"
        node.attributes["aria-sort"] = next_state
        self.sort_by = sort_by
        self.sort_direction = SortDirection.ASC if next_state == "ascending" else SortDirection.DESC
        self.page_number = 1
        self._emit_employee_response()

    def _next_page(self) -> None:
        self.page_number += 1
        self._emit_employee_response()

    def _query_pairs(self) -> list[tuple[str, str]]:
        field = "full_name" if self.sort_by == "name" else "current_contract"
        if self.sort_direction is SortDirection.DESC:
            field = f"-{field}"
        pairs = [
            ("search", self.search),
            ("filter_type", "and"),
            ("page", str(self.page_number)),
            ("per_page", "20"),
            ("sort", field),
            ("search_fields", "full_name,job_title,number,teams,email,tax_code"),
        ]
        if self.employee_filter is not EmployeeFilter.ALL:
            pairs.extend(
                (
                    ("filter[0][field]", "active"),
                    ("filter[0][op]", "="),
                    (
                        "filter[0][value]",
                        "1" if self.employee_filter is EmployeeFilter.ACTIVE else "0",
                    ),
                )
            )
        return pairs

    def _response_document(self) -> dict[str, Any]:
        data = self.api_pages.get(self.page_number, [])
        last_page = max(1, (self.api_total + 19) // 20)
        start = (self.page_number - 1) * 20 + 1 if data else None
        end = start + len(data) - 1 if start is not None else None
        endpoint = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
        return {
            "current_page": self.page_number,
            "data": data,
            "first_page_url": f"{endpoint}?page=1",
            "from": start,
            "last_page": last_page,
            "last_page_url": f"{endpoint}?page={last_page}",
            "links": [
                {"url": None, "label": "Previous", "active": False},
                {
                    "url": f"{endpoint}?page={self.page_number}",
                    "label": str(self.page_number),
                    "active": True,
                },
                {"url": None, "label": "Next", "active": False},
            ],
            "next_page_url": (
                f"{endpoint}?page={self.page_number + 1}" if self.page_number < last_page else None
            ),
            "path": endpoint,
            "per_page": 20,
            "prev_page_url": (
                f"{endpoint}?page={self.page_number - 1}" if self.page_number > 1 else None
            ),
            "to": end,
            "total": self.api_total,
        }

    def _emit_employee_response(self) -> None:
        endpoint = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
        self.emit_response(
            SyntheticResponse(
                f"{endpoint}?{urlencode(self._query_pairs())}", self._response_document()
            )
        )

    async def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> object:
        result = await super().goto(url, wait_until=wait_until, timeout=timeout)
        if url == f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}/it/app/employees/list":
            self._reset_state()
            self._emit_employee_response()
        return result


def _prepared(
    function_id: FunctionId,
    parameters: dict[str, JsonValue],
    *,
    employee_id: str | None = "EMP-SYNTH-001",
) -> PreparedAction:
    now = datetime.now(UTC)
    return PreparedAction(
        action_id=str(uuid4()),
        function_id=function_id,
        employee_id=employee_id,
        parameters=parameters,
        idempotency_key="idem-synthetic-001",  # gitleaks:allow -- synthetic fixture
        correlation_id="corr-synthetic-001",
        request_fingerprint="a" * 64,
        preview=(),
        required_approvals=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _control_page(*keys: str, confirmation: bool = True) -> SyntheticPage:
    page = SyntheticPage()
    for key in keys:
        page.add(key, SyntheticNode())
    if confirmation:
        page.add("common.confirm", SyntheticNode(visible=True))
        page.add(
            "common.confirm_dialog",
            SyntheticNode(
                text=(
                    "Alice Example EMP-SYNTH-001 CON-SYNTH-001 DOC-SYNTH-001 "
                    "Viewer Employee Ferie CV 2026 8"
                )
            ),
        )
    return page


def _row(fields: dict[str, str], *, attributes: dict[str, str] | None = None) -> SyntheticNode:
    row = SyntheticNode()
    for key, value in fields.items():
        row.add(
            key,
            SyntheticNode(
                text=value,
                attributes=attributes if key.endswith(".id") or key.endswith("_id") else None,
            ),
        )
    return row


def test_employee_list_uses_only_the_live_observed_stable_controls() -> None:
    expected_css = {
        "employees.filter.active": "dic-segmented-control-option#option-true",
        "employees.filter.inactive": "dic-segmented-control-option#option-false",
        "employees.filter.all": "dic-segmented-control-option#option-null",
        "employees.sort.name": "th.cdk-column-full_name",
        "employees.sort.contract": "th.cdk-column-current_contract",
        "employees.next": "dic-table-pagination button[text='Avanti']",
    }
    for key, selector in expected_css.items():
        candidates = DEFAULT_SELECTORS.candidates(key)
        assert [(candidate.kind, candidate.value) for candidate in candidates] == [
            (SelectorKind.CSS, selector)
        ]


@pytest.mark.asyncio
async def test_employee_list_reads_passive_redacted_response_and_paginates() -> None:
    page = EmployeeApiSyntheticPage()
    first = _api_employee(101, active=False)
    first["has_access"] = False
    second = _api_employee(102, name="Bob Example")
    page.api_pages = {
        1: [_api_employee(employee_id) for employee_id in range(1, 21)],
        2: [first, second, *[_api_employee(employee_id) for employee_id in range(103, 121)]],
    }
    page.api_total = 1_234
    result = await EmployeesListPage(
        page,
        "https://secure.dipendentincloud.it",
        expected_tenant_id="123456789",
    ).list(
        EmployeeListQuery(
            query="synthetic",
            employee_filter=EmployeeFilter.ACTIVE,
            sort_direction=SortDirection.ASC,
            page=2,
            page_size=25,
        )
    )

    assert result.total == 1234
    assert result.has_next is True
    assert result.page_size == 20
    assert len(result.items) == 20
    assert [item.employee_id for item in result.items[:2]] == ["101", "102"]
    assert result.items[0].display_name_redacted == "A. E."
    assert result.items[0].email_redacted == "s***@example.invalid"
    assert result.items[0].contract_state == "fixed_term"
    assert result.items[0].workplace == "Sede sintetica"
    assert result.items[0].account_state is AccountState.NOT_CONNECTED
    assert result.items[0].employee_state is EmployeeState.INACTIVE
    assert result.items[1].account_state is AccountState.CONNECTED
    assert page.next_button.clicks == 1


@pytest.mark.asyncio
async def test_employee_list_drops_private_browser_exception_context() -> None:
    private_marker = "PRIVATE_EMPLOYEE_BROWSER_FAILURE_MARKER"

    class PrivateResetFailurePage(EmployeeApiSyntheticPage):
        async def goto(
            self,
            url: str,
            *,
            wait_until: str | None = None,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            if url == "about:blank":
                raise RuntimeError(private_marker)
            return await super().goto(url, wait_until=wait_until, timeout=timeout)

    with pytest.raises(DicUiChangedError, match="response generation reset") as caught:
        await EmployeesListPage(
            PrivateResetFailurePage(),
            "https://secure.dipendentincloud.it",
        ).list(EmployeeListQuery())

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_marker not in repr(caught.value)


@pytest.mark.asyncio
async def test_employee_list_applies_descending_sort_to_an_unsorted_column() -> None:
    page = EmployeeApiSyntheticPage()

    result = await EmployeesListPage(page, "https://secure.dipendentincloud.it").list(
        EmployeeListQuery(sort_by="contract", sort_direction=SortDirection.DESC)
    )

    assert result.items == ()
    assert page.contract_sort.clicks == 2
    assert page.contract_sort.attributes["aria-sort"] == "descending"


@pytest.mark.asyncio
async def test_employee_list_reports_unverifiable_sort_and_missing_row_id() -> None:
    class InvalidSortPage(EmployeeApiSyntheticPage):
        def _reset_state(self) -> None:
            super()._reset_state()
            self.name_sort.attributes["aria-sort"] = "unexpected"

    page = InvalidSortPage()
    employees = EmployeesListPage(page, "https://secure.dipendentincloud.it")
    with pytest.raises(DicUiChangedError, match="sort state"):
        await employees.list(EmployeeListQuery())

    empty_row = SyntheticNode()
    with pytest.raises(DicUiChangedError, match="stable employee identifier"):
        await employees._employee_id(SyntheticLocator([empty_row]))


@pytest.mark.asyncio
async def test_employee_list_waits_for_hydration_and_rejects_nonessential_consent() -> None:
    class HydratingConsentPage(EmployeeApiSyntheticPage):
        async def goto(
            self,
            url: str,
            *,
            wait_until: str | None = None,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            result = await super().goto(url, wait_until=wait_until, timeout=timeout)
            if url.endswith("/it/app/employees/list"):
                for control in (
                    self.active_filter,
                    self.inactive_filter,
                    self.all_filter,
                ):
                    control.visible = False
                asyncio.get_running_loop().call_later(
                    0.01,
                    lambda: [
                        setattr(control, "visible", True)
                        for control in (
                            self.active_filter,
                            self.inactive_filter,
                            self.all_filter,
                        )
                    ],
                )
            return result

    page = HydratingConsentPage()
    banner = SyntheticNode(visible=True)
    reject = SyntheticNode(
        visible=True,
        on_click=lambda _node: (
            setattr(banner, "visible", False),
            setattr(reject, "visible", False),
        ),
    )
    page.add("consent.onetrust_banner", banner)
    page.add("consent.reject_nonessential", reject)

    result = await EmployeesListPage(
        page, "https://secure.dipendentincloud.it", timeout_ms=1_000
    ).list(EmployeeListQuery())

    assert result.items == ()
    assert reject.clicks == 1
    assert banner.visible is False


@pytest.mark.asyncio
async def test_employee_list_fails_closed_for_unrejectable_consent_and_route_race() -> None:
    blocked = EmployeeApiSyntheticPage()
    blocked.add("consent.onetrust_banner", SyntheticNode(visible=True))
    with pytest.raises(DicUiChangedError, match="cannot be rejected"):
        await EmployeesListPage(blocked, "https://secure.dipendentincloud.it", timeout_ms=100).list(
            EmployeeListQuery()
        )

    raced = EmployeeApiSyntheticPage()

    def navigate_away(_node: SyntheticNode) -> None:
        raced._url = "https://evil.invalid/it/app/employees/list"

    raced.all_filter.on_click = navigate_away
    with pytest.raises(DicUiChangedError, match="unexpected route"):
        await EmployeesListPage(raced, "https://secure.dipendentincloud.it", timeout_ms=100).list(
            EmployeeListQuery(employee_filter=EmployeeFilter.ALL)
        )


@pytest.mark.asyncio
async def test_employee_list_rejects_consent_before_it_waits_for_blocked_hydration() -> None:
    page = EmployeeApiSyntheticPage()
    for control in (page.active_filter, page.inactive_filter, page.all_filter):
        control.visible = False
    banner = SyntheticNode(visible=True)
    reject = SyntheticNode(visible=True)

    def unlock_hydration(_node: SyntheticNode) -> None:
        banner.visible = False
        reject.visible = False
        for control in (page.active_filter, page.inactive_filter, page.all_filter):
            control.visible = True

    reject.on_click = unlock_hydration
    page.add("consent.onetrust_banner", banner)
    page.add("consent.reject_nonessential", reject)

    result = await EmployeesListPage(
        page, "https://secure.dipendentincloud.it", timeout_ms=500
    ).list(EmployeeListQuery())

    assert result.total == 0
    assert reject.clicks == 1


@pytest.mark.asyncio
async def test_employee_list_rechecks_route_after_exact_response_body_finishes() -> None:
    class LateRedirectPage(EmployeeApiSyntheticPage):
        def _emit_employee_response(self) -> None:
            endpoint = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
            self.emit_response(
                SyntheticResponse(
                    f"{endpoint}?{urlencode(self._query_pairs())}",
                    self._response_document(),
                    on_body=lambda: setattr(
                        self, "_url", "https://evil.invalid/it/app/employees/list"
                    ),
                )
            )

    with pytest.raises(DicUiChangedError, match="unexpected route"):
        await EmployeesListPage(
            LateRedirectPage(),
            "https://secure.dipendentincloud.it",
            timeout_ms=500,
        ).list(EmployeeListQuery())


@pytest.mark.asyncio
async def test_employee_list_finishes_each_response_body_before_the_next_ui_action() -> None:
    class DelayedResponse(SyntheticResponse):
        def __init__(
            self,
            url: str,
            document: dict[str, Any],
            *,
            started: asyncio.Event,
            release: asyncio.Event,
        ) -> None:
            super().__init__(url, document)
            self.started = started
            self.release = release

        async def body(self) -> bytes:
            self.started.set()
            await self.release.wait()
            return await super().body()

    class DelayedSearchPage(EmployeeApiSyntheticPage):
        def __init__(self) -> None:
            super().__init__()
            self.body_started = asyncio.Event()
            self.body_release = asyncio.Event()

        def _search_filled(self, _node: SyntheticNode, value: str) -> None:
            self.search = value
            self.page_number = 1
            endpoint = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
            self.emit_response(
                DelayedResponse(
                    f"{endpoint}?{urlencode(self._query_pairs())}",
                    self._response_document(),
                    started=self.body_started,
                    release=self.body_release,
                )
            )

    page = DelayedSearchPage()
    operation = asyncio.create_task(
        EmployeesListPage(page, "https://secure.dipendentincloud.it", timeout_ms=1_000).list(
            EmployeeListQuery(query="synthetic", employee_filter=EmployeeFilter.INACTIVE)
        )
    )
    await asyncio.wait_for(page.body_started.wait(), timeout=1)
    assert page.inactive_filter.clicks == 0
    page.body_release.set()
    result = await operation
    assert result.total == 0
    assert page.inactive_filter.clicks == 1


@pytest.mark.asyncio
async def test_employee_create_fills_only_allowlisted_fields_and_rejects_unsafe_modes() -> None:
    keys = (
        "employees.new",
        "employees.create_manual",
        "employees.create_save",
        "summary.first_name",
        "summary.last_name",
        "summary.job_title",
    )
    page = _control_page(*keys)
    employee_page = EmployeesListPage(page, "https://secure.dipendentincloud.it")
    await employee_page.create_employee(
        _prepared(
            FunctionId.EMP_CREATE_001,
            {"first_name": "Alice", "last_name": "Example", "job_title": "Tester"},
            employee_id=None,
        )
    )
    assert page.root.children["summary.first_name"][0].filled == ["Alice"]
    assert page.root.children["employees.create_save"][0].clicks == 1

    with pytest.raises(DicValidationError, match="manual creation"):
        await employee_page.create_employee(
            _prepared(
                FunctionId.EMP_CREATE_001,
                {"creation_mode": "payroll", "first_name": "A", "last_name": "E"},
                employee_id=None,
            )
        )
    with pytest.raises(DicValidationError, match="first_name"):
        await employee_page.create_employee(
            _prepared(FunctionId.EMP_CREATE_001, {"last_name": "E"}, employee_id=None)
        )


@pytest.mark.asyncio
async def test_employee_create_baseline_requires_one_new_stable_exact_match() -> None:
    page = EmployeeApiSyntheticPage()
    page.api_pages = {1: [_api_employee(101)]}
    page.api_total = 1
    existing = _row(
        {"row.employee_id": "", "row.name": "Alice Example"},
        attributes={"data-employee-id": "101"},
    )
    page.add("employees.rows", existing)
    employee_page = EmployeesListPage(page, "https://secure.dipendentincloud.it")
    parameters = {"first_name": "Alice", "last_name": "Example", "job_title": "Tester"}
    baseline = await employee_page.stable_employee_ids_for_create(parameters)
    assert baseline == frozenset({"101"})

    created = _row(
        {
            "row.employee_id": "",
            "row.name": "Alice Example",
            "row.job_title": "Tester",
        },
        attributes={"data-employee-id": "102"},
    )
    page.add("employees.rows", created)
    page.api_pages = {1: [_api_employee(101), _api_employee(102)]}
    page.api_total = 2
    assert await employee_page.verify_created_employee(baseline, parameters) is True
    created.children["row.job_title"][0].text = "Wrong"
    assert await employee_page.verify_created_employee(baseline, parameters) is False


@pytest.mark.asyncio
async def test_summary_read_redacts_fields_and_executes_allowlisted_controls() -> None:
    page = _control_page(
        "summary.first_name",
        "summary.last_name",
        "summary.payroll_number",
        "summary.tax_code",
        "summary.birth_date",
        "summary.iban",
        "summary.job_title",
        "summary.phone",
        "summary.email",
        "summary.address",
        "summary.workplace",
        "summary.notes",
        "summary.state",
        "summary.save",
        "summary.connect",
    )
    values = {
        "summary.first_name": "Alice",
        "summary.last_name": "Example",
        "summary.payroll_number": "M-001",
        "summary.tax_code": "SYNTHETIC1234",
        "summary.birth_date": "2000-01-02",
        "summary.iban": "CH0000000000000000000",
        "summary.job_title": "Tester",
        "summary.phone": "+41000000000",
        "summary.email": "alice@example.invalid",
        "summary.address": "Synthetic street",
        "summary.workplace": "Lab",
        "summary.notes": "Synthetic note",
        "summary.state": "Attivo",
    }
    for key, value in values.items():
        page.root.children[key][0].value = value
    summary_page = EmployeeSummaryPage(page, "https://secure.dipendentincloud.it")
    summary = await summary_page.read("EMP-SYNTH-001")
    assert summary.first_name_redacted == "A."
    assert summary.tax_code_redacted == "*********1234"
    assert summary.address_redacted == "[REDACTED]"
    assert summary.notes_redacted == "[REDACTED]"
    assert summary.state is EmployeeState.ACTIVE

    expected_raw = {
        "first_name": "Alice",
        "last_name": "Example",
        "payroll_number": "M-001",
        "tax_code": "SYNTHETIC1234",
        "birth_date": "2000-01-02",
        "iban": "CH0000000000000000000",
        "job_title": "Tester",
        "phone": "+41000000000",
        "business_email": "alice@example.invalid",
        "address": "Synthetic street",
        "workplace": "Lab",
        "notes": "Synthetic note",
    }
    assert await summary_page.verify_expected("EMP-SYNTH-001", expected_raw) is True
    assert (
        await summary_page.verify_expected("EMP-SYNTH-001", {**expected_raw, "tax_code": "WRONG"})
        is False
    )

    await summary_page.execute(
        _prepared(FunctionId.EMP_UPDATE_001, {"job_title": "Synthetic lead"})
    )
    await summary_page.execute(_prepared(FunctionId.EMP_CONNECT_001, {}))
    assert page.root.children["summary.job_title"][0].filled[-1] == "Synthetic lead"
    assert page.root.children["summary.connect"][0].clicks == 1

    with pytest.raises(DicValidationError, match="not handled"):
        await summary_page.execute(_prepared(FunctionId.EMP_BAL_002, {}))
    with pytest.raises(DicValidationError, match="employee_id"):
        await summary_page.execute(_prepared(FunctionId.EMP_UPDATE_001, {}, employee_id=None))


@pytest.mark.asyncio
async def test_roles_and_timestamp_pages_cover_native_and_aria_checkbox_states() -> None:
    page = _control_page(
        "roles.time.timestamping",
        "roles.time.attendance",
        "roles.time.shifts",
        "roles.time.expenses",
        "roles.save",
    )
    page.add("roles.groups", SyntheticNode(text="HR"), SyntheticNode(text=""))
    page.add(
        "roles.items",
        SyntheticNode(text="Editor", attributes={"aria-checked": "true"}),
        SyntheticNode(text="Viewer", attributes={"aria-checked": "false"}),
    )
    page.root.children["roles.time.timestamping"][0].checked = True
    roles_page = EmployeeRolesPage(page, "https://secure.dipendentincloud.it")
    roles = await roles_page.read_roles("EMP-SYNTH-001")
    access = await roles_page.read_time_access("EMP-SYNTH-001")
    assert roles.groups == ("HR",)
    assert [role.enabled for role in roles.roles] == [True, False]
    assert access.timestamping_enabled is True

    await roles_page.execute(
        _prepared(
            FunctionId.EMP_RBAC_002,
            {"role_name": "Viewer", "enabled": True},
        )
    )
    assert page.root.children["roles.items"][1].checked is True
    with pytest.raises(DicValidationError, match="already matches"):
        await roles_page.execute(
            _prepared(FunctionId.EMP_RBAC_002, {"role_name": "viewer", "enabled": True})
        )
    with pytest.raises(DicValidationError, match="exactly one role"):
        await roles_page.execute(
            _prepared(FunctionId.EMP_RBAC_002, {"role_name": "Missing", "enabled": True})
        )
    with pytest.raises(DicValidationError, match="requires only"):
        await roles_page.execute(_prepared(FunctionId.EMP_RBAC_002, {"expense_access": "yes"}))

    timestamps_page = SyntheticPage()
    other = _row({"timestamps.row.employee_id": ""}, attributes={"data-employee-id": "EMP-OTHER"})
    target = _row(
        {"timestamps.row.employee_id": ""},
        attributes={"data-employee-id": "EMP-SYNTH-001"},
    )
    target.add(
        "timestamps.row.enabled",
        SyntheticNode(attributes={"aria-checked": "true"}, checked_error=True),
    )
    timestamps_page.add("timestamps.rows", other, target)
    enabled = await TimestampEmployeesPage(
        timestamps_page, "https://secure.dipendentincloud.it"
    ).read_enabled("EMP-SYNTH-001")
    assert enabled is True


@pytest.mark.asyncio
async def test_identity_bound_confirmation_refuses_wrong_modal_target() -> None:
    page = _control_page("summary.deactivate")
    page.root.children["common.confirm_dialog"][0].text = "EMP-SYNTH-OTHER"
    summary_page = EmployeeSummaryPage(page, "https://secure.dipendentincloud.it")

    with pytest.raises(DicUiChangedError, match="approved target"):
        await summary_page.execute(_prepared(FunctionId.EMP_STATUS_001, {}))

    assert page.root.children["common.confirm"][0].clicks == 0


@pytest.mark.asyncio
async def test_contract_page_reads_rows_and_executes_create_and_delete() -> None:
    page = _control_page(
        "contracts.new",
        "contracts.edit",
        "contracts.delete",
        "contracts.schedule",
        "contracts.flexibility",
        "contracts.permanent",
        "contracts.start_date",
        "contracts.end_date",
        "contracts.ccnl_level",
        "contracts.work_regime",
        "contracts.description",
        "contracts.type",
        "contracts.save",
    )
    row = _row(
        {
            "contract_row.id": "",
            "contract_row.schedule": "40h",
            "contract_row.flexibility": "No",
            "contract_row.permanent": "si",
            "contract_row.start_date": "2026-01-01",
            "contract_row.end_date": "",
            "contract_row.ccnl_level": "L1",
            "contract_row.work_regime": "Full time",
            "contract_row.description": "Synthetic description",
            "contract_row.type": "Indeterminato",
            "contract_row.status": "Attivo",
            "contract_row.period": "2026",
        },
        attributes={"data-contract-id": "CON-SYNTH-001"},
    )
    row.add("contracts.edit", page.root.children["contracts.edit"][0])
    row.add("contracts.delete", page.root.children["contracts.delete"][0])
    fallback_row = _row(
        {
            "contract_row.start_date": "2025-01-01",
            "contract_row.type": "Determinato",
            "contract_row.period": "2025",
        }
    )
    page.add("contracts.rows", row, fallback_row)
    contract_page = EmployeeContractsPage(page, "https://secure.dipendentincloud.it")
    records = await contract_page.read("EMP-SYNTH-001")
    assert records[0].permanent is True
    assert records[0].description == "[REDACTED]"
    assert records[0].stable_identifier is True
    assert records[0].actionable is True
    assert records[1].contract_id.startswith("CON-")
    assert records[1].stable_identifier is False
    assert records[1].actionable is False
    assert (
        await contract_page.verify_expected(
            "EMP-SYNTH-001",
            "CON-SYNTH-001",
            {
                "contract_id": "CON-SYNTH-001",
                "schedule": " 40H ",
                "description": "Synthetic description",
            },
        )
        is True
    )
    assert (
        await contract_page.verify_expected(
            "EMP-SYNTH-001",
            "CON-SYNTH-001",
            {"contract_id": "CON-SYNTH-001", "schedule": "36h"},
        )
        is False
    )
    assert (
        await contract_page.verify_expected(
            "EMP-SYNTH-001",
            "CON-0123456789abcdef",
            {"contract_id": "CON-0123456789abcdef", "schedule": "40h"},
        )
        is None
    )
    assert (
        await contract_page.verify_created_contract(
            "EMP-SYNTH-001", frozenset(), {"schedule": "40h", "permanent": True}
        )
        is True
    )

    await contract_page.execute(
        _prepared(
            FunctionId.EMP_CONTRACT_002,
            {"schedule": "36h", "contract_type": "Determinato", "permanent": False},
        )
    )
    assert page.root.children["contracts.schedule"][0].filled == ["36h"]
    assert page.root.children["contracts.permanent"][0].checked is False
    await contract_page.execute(
        _prepared(FunctionId.EMP_CONTRACT_003, {"contract_id": "CON-SYNTH-001"})
    )
    assert page.root.children["contracts.delete"][0].clicks == 1

    with pytest.raises(DicNotFoundError):
        await contract_page.execute(
            _prepared(FunctionId.EMP_CONTRACT_003, {"contract_id": "CON-MISSING"})
        )
    with pytest.raises(DicValidationError, match="fallback contract"):
        await contract_page.execute(
            _prepared(
                FunctionId.EMP_CONTRACT_003,
                {"contract_id": "CON-0123456789abcdef"},
            )
        )
    with pytest.raises(DicValidationError, match="permanent must be boolean"):
        await contract_page.execute(_prepared(FunctionId.EMP_CONTRACT_002, {"permanent": "yes"}))


@pytest.mark.asyncio
async def test_maturation_balance_and_payroll_pages_read_and_validate_writes() -> None:
    maturation_dom = _control_page(
        "maturations.new",
        "maturations.category",
        "maturations.valid_from",
        "maturations.valid_to",
        "maturations.save",
    )
    maturation_dom.add(
        "maturations.rows",
        _row(
            {
                "maturation_row.id": "",
                "maturation_row.category": "ROL",
                "maturation_row.valid_from": "2026-01-01",
                "maturation_row.valid_to": "2026-12-31",
                "maturation_row.status": "Valida",
            },
            attributes={"data-maturation-id": "MAT-SYNTH-NEW"},
        ),
    )
    maturation_page = EmployeeMaturationsPage(maturation_dom, "https://secure.dipendentincloud.it")
    maturations = await maturation_page.read("EMP-SYNTH-001")
    assert maturations[0].maturation_id.startswith("MAT-")
    assert (
        await maturation_page.verify_created_maturation(
            "EMP-SYNTH-001", frozenset(), {"category": "ROL"}
        )
        is True
    )
    await maturation_page.execute(
        _prepared(
            FunctionId.EMP_MAT_002,
            {"category": "Ferie", "valid_from": "2026-01-01"},
        )
    )
    with pytest.raises(DicValidationError, match="category"):
        await maturation_page.execute(_prepared(FunctionId.EMP_MAT_002, {}))

    balance_dom = _control_page(
        "balance.year",
        "balance.month",
        "balance.correct",
        "balance.correction_month",
        "balance.category",
        "balance.amount",
        "balance.save",
    )
    balance_dom.add(
        "balance.rows",
        _row(
            {
                "balance_row.category": "Ferie",
                "balance_row.previous_year": "1",
                "balance_row.previous_month": "2",
                "balance_row.accrued": "3",
                "balance_row.used": "1",
                "balance_row.corrections": "0",
                "balance_row.current_residual": "4",
            }
        ),
    )
    balance_page = EmployeeBalancePage(balance_dom, "https://secure.dipendentincloud.it")
    balance = await balance_page.read("EMP-SYNTH-001", 2026)
    assert balance.lines[0].current_residual == "4"
    await balance_page.execute(
        _prepared(
            FunctionId.EMP_BAL_002,
            {
                "year": 2026,
                "month": 8,
                "category": "Ferie",
                "previous_value": "0",
                "amount": "1",
            },
        )
    )
    with pytest.raises(DicValidationError, match="precondition changed"):
        await balance_page.execute(
            _prepared(
                FunctionId.EMP_BAL_002,
                {
                    "year": 2026,
                    "month": 8,
                    "category": "Ferie",
                    "previous_value": "9",
                    "amount": "1",
                },
            )
        )
    with pytest.raises(DicValidationError, match="year must be an integer"):
        await balance_page.execute(
            _prepared(
                FunctionId.EMP_BAL_002,
                {
                    "year": True,
                    "month": 8,
                    "category": "Ferie",
                    "previous_value": "0",
                    "amount": "1",
                },
            )
        )

    payroll_dom = _control_page("payrolls.year")
    payroll_dom.add(
        "payrolls.rows",
        _row(
            {
                "payroll_row.year": "2026",
                "payroll_row.month": "7",
                "payroll_row.status": "Pubblicata",
                "payroll_row.published_at": "2026-07-31",
            }
        ),
    )
    payrolls = await EmployeePayrollsPage(payroll_dom, "https://secure.dipendentincloud.it").read(
        "EMP-SYNTH-001", 2026
    )
    assert payrolls[0].payroll_id.startswith("PAY-")
    assert payrolls[0].month == 7

    missing_year = SyntheticPage()
    missing_year.add("payrolls.rows", _row({"payroll_row.month": "7"}))
    with pytest.raises(DicUiChangedError, match="does not expose its year"):
        await EmployeePayrollsPage(missing_year, "https://secure.dipendentincloud.it").read(
            "EMP-SYNTH-001"
        )


@pytest.mark.asyncio
async def test_document_page_reads_filtered_metadata_and_executes_file_workflows(tmp_path) -> None:
    page = _control_page(
        "documents.pending",
        "documents.search",
        "documents.upload",
        "documents.file",
        "documents.title",
        "documents.category",
        "documents.expiry",
        "documents.save",
        "documents.edit",
        "documents.delete",
    )
    matching = _row(
        {
            "document_row.id": "",
            "document_row.title": "Synthetic CV",
            "document_row.category": "CV",
            "document_row.expiry": "2027-01-01",
            "document_row.uploaded_at": "2026-01-01",
            "document_row.uploaded_by": "Alice Example",
            "document_row.state": "In attesa",
        },
        attributes={"data-document-id": "DOC-SYNTH-001"},
    )
    matching.add("documents.edit", page.root.children["documents.edit"][0])
    matching.add("documents.delete", page.root.children["documents.delete"][0])
    ignored = _row({"document_row.title": "Other", "document_row.category": "Patente"})
    page.add("documents.rows", matching, ignored)
    documents_page = EmployeeDocumentsPage(page, "https://secure.dipendentincloud.it")
    documents = await documents_page.read(
        "EMP-SYNTH-001", DocumentQuery(query="synthetic", state="pending", category="CV")
    )
    assert len(documents) == 1
    assert documents[0].title_redacted == "[REDACTED]"
    assert documents[0].uploaded_by_redacted == "A. E."
    assert documents[0].state == "pending"
    assert documents[0].stable_identifier is True
    assert documents[0].actionable is True
    all_documents = await documents_page.read("EMP-SYNTH-001", DocumentQuery())
    fallback = next(record for record in all_documents if record.document_id != "DOC-SYNTH-001")
    assert fallback.stable_identifier is False
    assert fallback.actionable is False
    assert await documents_page.stable_document_ids("EMP-SYNTH-001") == frozenset({"DOC-SYNTH-001"})
    assert (
        await documents_page.verify_expected_metadata(
            "EMP-SYNTH-001",
            "DOC-SYNTH-001",
            {"document_id": "DOC-SYNTH-001", "category": "cv"},
        )
        is True
    )
    assert (
        await documents_page.verify_expected_metadata(
            "EMP-SYNTH-001",
            "DOC-SYNTH-001",
            {"document_id": "DOC-SYNTH-001", "expiry_date": "2030-01-01"},
        )
        is False
    )
    assert (
        await documents_page.verify_uploaded_document(
            "EMP-SYNTH-001",
            frozenset(),
            {"category": "CV"},
        )
        is True
    )
    assert (
        await documents_page.verify_uploaded_document(
            "EMP-SYNTH-001",
            frozenset({"DOC-SYNTH-001"}),
            {"category": "CV"},
        )
        is None
    )

    first_digest = await documents_page.opaque_state_digest(b"k" * 32, scope="documents")
    second_digest = await documents_page.opaque_state_digest(b"k" * 32, scope="documents")
    assert first_digest == second_digest
    assert len(first_digest) == 64
    assert "Synthetic" not in first_digest
    matching.children["document_row.title"][0].text = "Changed raw title"
    assert await documents_page.opaque_state_digest(b"k" * 32, scope="documents") != first_digest

    upload = (tmp_path / "synthetic.pdf").resolve()
    upload.write_bytes(b"%PDF-synthetic")
    await documents_page.execute(
        _prepared(
            FunctionId.EMP_DOC_002,
            {
                "category": "CV",
                "expiry_date": "2027-01-01",
            },
        ),
        verified_upload=VerifiedUploadPayload(
            name="document-upload.pdf",
            mime_type="application/pdf",
            buffer=upload.read_bytes(),
        ),
    )
    assert page.root.children["documents.file"][0].uploaded_files == [
        {
            "name": "document-upload.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-synthetic",
        }
    ]
    await documents_page.execute(
        _prepared(
            FunctionId.EMP_DOC_004,
            {"document_id": "DOC-SYNTH-001", "category": "Contratto"},
        )
    )
    await documents_page.execute(
        _prepared(FunctionId.EMP_DOC_005, {"document_id": "DOC-SYNTH-001"})
    )
    assert page.root.children["documents.edit"][0].clicks == 1
    assert page.root.children["documents.delete"][0].clicks == 1
    with pytest.raises(DicValidationError, match="fallback document"):
        await documents_page.execute(
            _prepared(
                FunctionId.EMP_DOC_005,
                {"document_id": "DOC-0123456789abcdef"},
            )
        )

    with pytest.raises(DicValidationError, match="not handled"):
        await documents_page.execute(_prepared(FunctionId.EMP_UPDATE_001, {}))
    with pytest.raises(DicValidationError, match="verified adapter payload"):
        await documents_page.execute(_prepared(FunctionId.EMP_DOC_002, {}))
