"""Typed Playwright-like primitives and route/selector safety helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypedDict
from urllib.parse import urljoin, urlparse

from bh_dic.dic.errors import DicConfigurationError, DicUiChangedError, DicValidationError
from bh_dic.dic.models import OpaqueStateDigest
from bh_dic.dic.route_registry import DIC_ROUTES
from bh_dic.dic.selectors import (
    DEFAULT_SELECTORS,
    SelectorCandidate,
    SelectorKind,
    SelectorRegistry,
)


class PlaywrightFilePayload(TypedDict):
    name: str
    mimeType: str
    buffer: bytes


@dataclass(frozen=True, slots=True)
class VerifiedUploadPayload:
    """In-memory file capability; content is deliberately absent from repr."""

    name: str
    mime_type: str
    buffer: bytes = field(repr=False)

    def as_playwright(self) -> PlaywrightFilePayload:
        return {
            "name": self.name,
            "mimeType": self.mime_type,
            "buffer": self.buffer,
        }


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

    async def evaluate(self, expression: str, argument: object = None) -> object: ...

    async def get_attribute(self, name: str) -> str | None: ...

    async def input_value(self) -> str: ...

    async def is_checked(self) -> bool: ...

    async def is_visible(self) -> bool: ...

    async def click(self) -> None: ...

    async def fill(self, value: str) -> None: ...

    async def select_option(self, value: str) -> None: ...

    async def set_checked(self, checked: bool) -> None: ...

    async def set_input_files(self, files: str | Sequence[str] | PlaywrightFilePayload) -> None: ...


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

    def on(self, event: str, handler: object) -> None: ...

    def remove_listener(self, event: str, handler: object) -> None: ...


EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class BaseDicPage:
    """Base page that only navigates within the configured HTTPS origin."""

    route_template: str

    _RAW_DOM_STATE_SCRIPT = """
    element => {
      const clean = value => (value || "").replace(/\\s+/g, " ").trim();
      const nodes = Array.from(element.querySelectorAll("*"));
      return nodes
        .filter(node => !["SCRIPT", "STYLE", "NOSCRIPT"].includes(node.tagName))
        .map(node => {
          const tag = node.tagName.toLowerCase();
          const leafText = node.children.length === 0 ? clean(node.textContent) : "";
          const state = {tag};
          if (leafText) state.text = leafText;
          if (node instanceof HTMLInputElement) {
            if (node.type !== "hidden") state.value = node.value;
            if (["checkbox", "radio"].includes(node.type)) state.checked = node.checked;
          } else if (node instanceof HTMLTextAreaElement) {
            state.value = node.value;
          } else if (node instanceof HTMLSelectElement) {
            state.value = node.value;
          }
          for (const name of [
            "data-employee-id", "data-contract-id", "data-maturation-id",
            "data-document-id", "data-payroll-id", "href", "aria-checked",
            "aria-disabled", "aria-selected"
          ]) {
            if (node.hasAttribute(name)) state[name] = node.getAttribute(name);
          }
          return state;
        })
        .filter(state => Object.keys(state).length > 1);
    }
    """

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
        route = DIC_ROUTES.for_template(self.route_template)
        return route.render(employee_id=employee_id)

    def absolute_url(self, employee_id: str | None = None) -> str:
        path = self.route(employee_id)
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if f"{urlparse(url).scheme}://{urlparse(url).netloc}" != self.base_url:
            raise DicConfigurationError("route escaped the configured DIC origin")
        return url

    async def navigate(self, employee_id: str | None = None) -> None:
        """Navigate from a fixed route while leaving redirect validation to the caller."""

        url = self.absolute_url(employee_id)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)

    async def open(self, employee_id: str | None = None) -> None:
        path = self.route(employee_id)
        await self.navigate(employee_id)
        current = urlparse(self.page.url)
        if f"{current.scheme}://{current.netloc}" != self.base_url:
            raise DicUiChangedError("navigation left the configured DIC origin")
        if current.path.rstrip("/") != path.rstrip("/"):
            raise DicUiChangedError("browser reached an unexpected DIC route")

    async def opaque_state_digest(
        self, state_digest_key: bytes, *, scope: str
    ) -> OpaqueStateDigest:
        """HMAC raw DOM state in-page and return no source values to callers."""

        if len(state_digest_key) < 32:
            raise DicConfigurationError("DIC state digest key must contain at least 32 bytes")
        body = self.page.locator("body")
        try:
            if await body.count() != 1:
                raise DicUiChangedError("DIC page body is unavailable for state digest")
            raw_state = await body.evaluate(self._RAW_DOM_STATE_SCRIPT)
            canonical = json.dumps(
                {
                    "schema": "bh-dic-dom-state-v1",
                    "scope": scope,
                    "route": urlparse(self.page.url).path,
                    "state": raw_state,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except DicUiChangedError:
            raise
        except Exception as exc:
            raise DicUiChangedError("DIC raw state could not be digested safely") from exc
        return hmac.new(state_digest_key, canonical, hashlib.sha256).hexdigest()

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

    async def confirm_if_present(
        self, *, expected_identity: str | Sequence[str] | None = None
    ) -> None:
        confirmation = await self.locate("common.confirm", required=False)
        if confirmation is None or not await confirmation.is_visible():
            if expected_identity is not None:
                raise DicUiChangedError("identity-bound confirmation dialog is unavailable")
            return
        if expected_identity is not None:
            dialog = await self.locate("common.confirm_dialog", required=False)
            if dialog is None or not await dialog.is_visible():
                raise DicUiChangedError("identity-bound confirmation dialog is unavailable")
            dialog_text = (await dialog.inner_text()).strip().casefold()
            expected = (
                (expected_identity,)
                if isinstance(expected_identity, str)
                else tuple(expected_identity)
            )
            if (
                not expected
                or any(not value.strip() for value in expected)
                or any(
                    re.search(
                        rf"(?<![\w-]){re.escape(value.strip().casefold())}(?![\w-])",
                        dialog_text,
                    )
                    is None
                    for value in expected
                )
            ):
                raise DicUiChangedError("confirmation dialog does not match the approved target")
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
