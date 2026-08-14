from __future__ import annotations

from typing import Self

import pytest

from bh_dic.dic.errors import DicUiChangedError
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
)
from bh_dic.dic.selectors import DEFAULT_SELECTORS


class EmptyLocator:
    @property
    def first(self) -> Self:
        return self

    def nth(self, index: int) -> Self:
        del index
        return self

    def locator(self, selector: str) -> Self:
        del selector
        return self

    def get_by_role(self, role: str, *, name=None, exact=None) -> Self:
        del role, name, exact
        return self

    def get_by_label(self, text: str, *, exact=None) -> Self:
        del text, exact
        return self

    def get_by_placeholder(self, text: str) -> Self:
        del text
        return self

    def get_by_test_id(self, test_id: str) -> Self:
        del test_id
        return self

    def get_by_text(self, text: str, *, exact=None) -> Self:
        del text, exact
        return self

    async def count(self) -> int:
        return 0

    async def inner_text(self) -> str:
        return ""

    async def get_attribute(self, name: str):
        del name
        return None

    async def input_value(self) -> str:
        raise RuntimeError

    async def is_checked(self) -> bool:
        return False

    async def is_visible(self) -> bool:
        return False

    async def click(self) -> None: ...

    async def fill(self, value: str) -> None:
        del value

    async def select_option(self, value: str) -> None:
        del value

    async def set_checked(self, checked: bool) -> None:
        del checked

    async def set_input_files(self, files) -> None:
        del files


class RoutePage(EmptyLocator):
    def __init__(self) -> None:
        self._url = ""
        self.visited: list[str] = []

    @property
    def url(self) -> str:
        return self._url

    async def goto(
        self,
        url: str,
        *,
        wait_until=None,
        timeout=None,  # noqa: ASYNC109
    ) -> object:
        del wait_until, timeout
        self._url = url
        self.visited.append(url)
        return object()

    async def wait_for_load_state(
        self,
        state=None,
        *,
        timeout=None,  # noqa: ASYNC109
    ) -> None:
        del state, timeout


class CrossOriginRedirectPage(RoutePage):
    async def goto(
        self,
        url: str,
        *,
        wait_until=None,
        timeout=None,  # noqa: ASYNC109
    ) -> object:
        await super().goto(url, wait_until=wait_until, timeout=timeout)
        self._url = "https://attacker.invalid/capture"
        return object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page_class", "expected"),
    [
        (EmployeesListPage, "/it/app/employees/list"),
        (EmployeeSummaryPage, "/it/app/employees/info/EMP-SYNTH-001/summary"),
        (EmployeeRolesPage, "/it/app/employees/info/EMP-SYNTH-001/roles"),
        (EmployeeContractsPage, "/it/app/employees/info/EMP-SYNTH-001/contracts"),
        (EmployeeMaturationsPage, "/it/app/employees/info/EMP-SYNTH-001/maturations"),
        (EmployeeBalancePage, "/it/app/employees/info/EMP-SYNTH-001/counters"),
        (EmployeePayrollsPage, "/it/app/employees/info/EMP-SYNTH-001/payrolls"),
        (
            EmployeeDocumentsPage,
            "/it/app/employees/info/EMP-SYNTH-001/documents/list",
        ),
        (TimestampEmployeesPage, "/it/app/settings/timestamps/employees"),
    ],
)
async def test_every_page_object_navigates_only_to_declared_route(page_class, expected) -> None:
    browser_page = RoutePage()
    page = page_class(browser_page, "https://secure.dipendentincloud.it")
    employee_id = (
        "EMP-SYNTH-001"
        if page_class
        not in {
            EmployeesListPage,
            TimestampEmployeesPage,
        }
        else None
    )
    await page.open(employee_id)
    assert browser_page.visited == [f"https://secure.dipendentincloud.it{expected}"]


def test_selector_registry_covers_every_route_family() -> None:
    prefixes = {key.split(".", 1)[0] for key in DEFAULT_SELECTORS.keys}
    assert {
        "auth",
        "employees",
        "summary",
        "roles",
        "timestamps",
        "contracts",
        "maturations",
        "balance",
        "payrolls",
        "documents",
    } <= prefixes


@pytest.mark.asyncio
async def test_missing_required_selector_is_reported_as_ui_drift() -> None:
    browser_page = RoutePage()
    page = EmployeesListPage(browser_page, "https://secure.dipendentincloud.it")
    await page.open()
    with pytest.raises(DicUiChangedError, match="selector candidate"):
        await page.click("employees.new")


def test_page_redaction_helpers_do_not_return_raw_personal_values() -> None:
    browser_page = RoutePage()
    page = EmployeesListPage(browser_page, "https://secure.dipendentincloud.it")
    assert page.redact_name("Alice Example") == "A. E."
    assert page.redact_email("alice@example.invalid") == "a***@example.invalid"
    assert page.redact_tail("ABCDEF1234567890") == "************7890"


@pytest.mark.asyncio
async def test_page_object_rejects_cross_origin_redirect() -> None:
    page = EmployeesListPage(CrossOriginRedirectPage(), "https://secure.dipendentincloud.it")
    with pytest.raises(DicUiChangedError, match="left the configured DIC origin"):
        await page.open()
