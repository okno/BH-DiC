from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

import pytest
from pydantic import JsonValue

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
    ) -> None:
        self.text = text
        self.value = value
        self.attributes = attributes or {}
        self.checked = checked
        self.visible = visible
        self.checked_error = checked_error
        self.on_click = on_click
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


@pytest.mark.asyncio
async def test_employee_list_reads_redacted_synthetic_dom_and_paginates() -> None:
    page = _control_page("employees.search", "employees.filter.active", "employees.next")
    sort = SyntheticNode(
        attributes={"aria-sort": "descending"},
        on_click=lambda node: node.attributes.update({"aria-sort": "ascending"}),
    )
    page.add("employees.sort.name", sort)
    page.add("employees.total", SyntheticNode(text="Totale 1.234"))
    first = _row(
        {
            "row.employee_id": "",
            "row.name": "Alice Example",
            "row.email": "alice@example.invalid",
            "row.tax_code": "SYNTHETIC1234",
            "row.job_title": "Tester",
            "row.group": "Synthetic",
            "row.payroll_number": "M-001",
            "row.contract": "Indeterminato",
            "row.contract_state": "Attivo",
            "row.contract_period": "2026",
            "row.schedule": "40h",
            "row.workplace": "Sede sintetica",
            "row.account_state": "Non collegato",
            "row.employee_state": "Disattivato",
        },
        attributes={"data-employee-id": "EMP-SYNTH-001"},
    )
    second = _row(
        {
            "row.employee_id": "",
            "row.name": "Bob Example",
            "row.account_state": "Collegato",
            "row.employee_state": "Attivo",
        },
        attributes={"href": "/it/app/employees/info/EMP-SYNTH-002/summary"},
    )
    page.add("employees.rows", first, second)
    result = await EmployeesListPage(page, "https://secure.dipendentincloud.it").list(
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
    assert [item.employee_id for item in result.items] == ["EMP-SYNTH-001", "EMP-SYNTH-002"]
    assert result.items[0].display_name_redacted == "A. E."
    assert result.items[0].email_redacted == "a***@example.invalid"
    assert result.items[0].contract_state == "Attivo"
    assert result.items[0].workplace == "Sede sintetica"
    assert result.items[0].account_state is AccountState.NOT_CONNECTED
    assert result.items[0].employee_state is EmployeeState.INACTIVE
    assert result.items[1].account_state is AccountState.CONNECTED
    assert sort.clicks == 1


@pytest.mark.asyncio
async def test_employee_list_applies_descending_sort_to_an_unsorted_column() -> None:
    page = _control_page("employees.filter.active")
    page.add("employees.container", SyntheticNode())
    page.add("employees.total", SyntheticNode(text="Totale 0"))

    def advance_sort(node: SyntheticNode) -> None:
        current = node.attributes.get("aria-sort")
        node.attributes["aria-sort"] = "ascending" if current == "none" else "descending"

    sort = SyntheticNode(attributes={"aria-sort": "none"}, on_click=advance_sort)
    page.add("employees.sort.contract", sort)

    result = await EmployeesListPage(page, "https://secure.dipendentincloud.it").list(
        EmployeeListQuery(sort_by="contract", sort_direction=SortDirection.DESC)
    )

    assert result.items == ()
    assert sort.clicks == 2
    assert sort.attributes["aria-sort"] == "descending"


@pytest.mark.asyncio
async def test_employee_list_reports_unverifiable_sort_and_missing_row_id() -> None:
    page = _control_page("employees.filter.active")
    page.add("employees.sort.name", SyntheticNode(attributes={"aria-sort": "unexpected"}))
    employees = EmployeesListPage(page, "https://secure.dipendentincloud.it")
    with pytest.raises(DicUiChangedError, match="sort state"):
        await employees.list(EmployeeListQuery())

    empty_row = SyntheticNode()
    with pytest.raises(DicUiChangedError, match="stable employee identifier"):
        await employees._employee_id(SyntheticLocator([empty_row]))


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
    page = _control_page("employees.search", "employees.filter.all", confirmation=False)
    page.add("employees.sort.name", SyntheticNode(attributes={"aria-sort": "ascending"}))
    page.add("employees.total", SyntheticNode(text="Totale 1"))
    existing = _row(
        {"row.employee_id": "", "row.name": "Alice Example"},
        attributes={"data-employee-id": "EMP-SYNTH-OLD"},
    )
    page.add("employees.rows", existing)
    employee_page = EmployeesListPage(page, "https://secure.dipendentincloud.it")
    parameters = {"first_name": "Alice", "last_name": "Example", "job_title": "Tester"}
    baseline = await employee_page.stable_employee_ids_for_create(parameters)
    assert baseline == frozenset({"EMP-SYNTH-OLD"})

    created = _row(
        {
            "row.employee_id": "",
            "row.name": "Alice Example",
            "row.job_title": "Tester",
        },
        attributes={"data-employee-id": "EMP-SYNTH-NEW"},
    )
    page.add("employees.rows", created)
    page.root.children["employees.total"][0].text = "Totale 2"
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
