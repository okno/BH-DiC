"""Typed Playwright-like primitives and route/selector safety helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urljoin, urlparse

from bh_dic.dic.errors import DicConfigurationError, DicUiChangedError, DicValidationError
from bh_dic.dic.selectors import (
    DEFAULT_SELECTORS,
    SelectorCandidate,
    SelectorKind,
    SelectorRegistry,
)


class LocatorLike(Protocol):
    @property
    def first(self) -> LocatorLike: ...

    def nth(self, index: int) -> LocatorLike: ...

    def locator(self, selector: str) -> LocatorLike: ...

    def get_by_role(
        self, role: str, *, name: str | None = None, exact: bool | None = None
    ) -> LocatorLike: ...

    def get_by_label(self, text: str, *, exact: bool | None = None) -> LocatorLike: ...

    def get_by_placeholder(self, text: str) -> LocatorLike: ...

    def get_by_test_id(self, test_id: str) -> LocatorLike: ...

    def get_by_text(self, text: str, *, exact: bool | None = None) -> LocatorLike: ...

    async def count(self) -> int: ...

    async def inner_text(self) -> str: ...

    async def get_attribute(self, name: str) -> str | None: ...

    async def input_value(self) -> str: ...

    async def is_checked(self) -> bool: ...

    async def is_visible(self) -> bool: ...

    async def click(self) -> None: ...

    async def fill(self, value: str) -> None: ...

    async def select_option(self, value: str) -> None: ...

    async def set_checked(self, checked: bool) -> None: ...

    async def set_input_files(self, files: str | Sequence[str]) -> None: ...


class PageLike(LocatorLike, Protocol):
    @property
    def url(self) -> str: ...

    async def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> object: ...

    async def wait_for_load_state(
        self,
        state: str | None = None,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> None: ...


EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class BaseDicPage:
    """Base page that only navigates within the configured HTTPS origin."""

    route_template: str

    def __init__(
        self,
        page: PageLike,
        base_url: str,
        *,
        selectors: SelectorRegistry = DEFAULT_SELECTORS,
        timeout_ms: float = 15_000,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise DicConfigurationError("DIC base_url must be a credential-free HTTPS origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise DicConfigurationError("DIC base_url must not include a path, query, or fragment")
        self.page = page
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.selectors = selectors
        self.timeout_ms = timeout_ms

    @staticmethod
    def validate_employee_id(employee_id: str) -> str:
        if EMPLOYEE_ID_PATTERN.fullmatch(employee_id) is None:
            raise DicValidationError("invalid employee identifier")
        return employee_id

    def route(self, employee_id: str | None = None) -> str:
        if "{employee_id}" not in self.route_template:
            if employee_id is not None:
                raise DicValidationError("this route does not accept employee_id")
            return self.route_template
        if employee_id is None:
            raise DicValidationError("employee_id is required by this route")
        return self.route_template.format(employee_id=self.validate_employee_id(employee_id))

    async def open(self, employee_id: str | None = None) -> None:
        path = self.route(employee_id)
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if f"{urlparse(url).scheme}://{urlparse(url).netloc}" != self.base_url:
            raise DicConfigurationError("route escaped the configured DIC origin")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        current = urlparse(self.page.url)
        if f"{current.scheme}://{current.netloc}" != self.base_url:
            raise DicUiChangedError("navigation left the configured DIC origin")
        if current.path.rstrip("/") != path.rstrip("/"):
            raise DicUiChangedError("browser reached an unexpected DIC route")

    @staticmethod
    def _candidate_locator(root: LocatorLike, candidate: SelectorCandidate) -> LocatorLike:
        if candidate.kind is SelectorKind.ROLE:
            return root.get_by_role(
                candidate.value, name=candidate.name, exact=candidate.exact or None
            )
        if candidate.kind is SelectorKind.LABEL:
            return root.get_by_label(candidate.value, exact=candidate.exact or None)
        if candidate.kind is SelectorKind.PLACEHOLDER:
            return root.get_by_placeholder(candidate.value)
        if candidate.kind is SelectorKind.TEST_ID:
            return root.get_by_test_id(candidate.value)
        if candidate.kind is SelectorKind.TEXT:
            return root.get_by_text(candidate.value, exact=candidate.exact or None)
        if candidate.kind is SelectorKind.CSS:
            return root.locator(candidate.value)
        raise DicConfigurationError(f"unsupported selector kind: {candidate.kind}")

    async def locate(
        self, key: str, *, root: LocatorLike | None = None, required: bool = True
    ) -> LocatorLike | None:
        scope: LocatorLike = root or self.page
        last_error: Exception | None = None
        for candidate in self.selectors.candidates(key):
            locator = self._candidate_locator(scope, candidate)
            try:
                count = await locator.count()
            except Exception as exc:
                last_error = exc
            else:
                if count > 0:
                    return locator.first
        if required:
            raise DicUiChangedError(
                f"no selector candidate matched registry key {key!r}"
            ) from last_error
        return None

    async def all_matches(self, key: str, *, root: LocatorLike | None = None) -> LocatorLike:
        scope: LocatorLike = root or self.page
        for candidate in self.selectors.candidates(key):
            locator = self._candidate_locator(scope, candidate)
            try:
                count = await locator.count()
            except Exception:
                count = 0
            if count > 0:
                return locator
        return scope.locator(":not(*)")

    async def read_text(
        self, key: str, *, root: LocatorLike | None = None, default: str | None = None
    ) -> str | None:
        locator = await self.locate(key, root=root, required=False)
        if locator is None:
            return default
        try:
            value = (await locator.input_value()).strip()
        except Exception:
            value = (await locator.inner_text()).strip()
        return value or default

    async def read_attribute(
        self, key: str, attribute: str, *, root: LocatorLike | None = None
    ) -> str | None:
        locator = await self.locate(key, root=root, required=False)
        if locator is None:
            return None
        return await locator.get_attribute(attribute)

    async def fill(self, key: str, value: str, *, root: LocatorLike | None = None) -> None:
        locator = await self.locate(key, root=root)
        if locator is None:
            raise DicUiChangedError("required form control is unavailable")
        await locator.fill(value)

    async def select(self, key: str, value: str) -> None:
        locator = await self.locate(key)
        if locator is None:
            raise DicUiChangedError("required select control is unavailable")
        await locator.select_option(value)

    async def click(self, key: str, *, root: LocatorLike | None = None) -> None:
        locator = await self.locate(key, root=root)
        if locator is None:
            raise DicUiChangedError("required action control is unavailable")
        await locator.click()

    async def is_checked(self, key: str) -> bool | None:
        locator = await self.locate(key, required=False)
        if locator is None:
            return None
        try:
            return await locator.is_checked()
        except Exception:
            return None

    async def confirm_if_present(self) -> None:
        confirmation = await self.locate("common.confirm", required=False)
        if confirmation is not None and await confirmation.is_visible():
            await confirmation.click()

    @staticmethod
    def redact_name(value: str | None) -> str | None:
        if not value:
            return None
        words = [part for part in value.strip().split() if part]
        return " ".join(f"{part[0].upper()}." for part in words)

    @staticmethod
    def redact_email(value: str | None) -> str | None:
        if not value or "@" not in value:
            return "[REDACTED]" if value else None
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"

    @staticmethod
    def redact_tail(value: str | None, visible: int = 4) -> str | None:
        if not value:
            return None
        compact = value.strip()
        if len(compact) <= visible:
            return "*" * len(compact)
        return f"{'*' * (len(compact) - visible)}{compact[-visible:]}"
