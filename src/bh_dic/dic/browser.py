"""Minimal hardened async Playwright Chromium lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue

from bh_dic.dic.errors import DicConfigurationError
from bh_dic.dic.pages import PageLike

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Playwright


@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    headless: bool = True
    locale: str = "it-IT"
    timezone_id: str = "Europe/Zurich"
    navigation_timeout_ms: float = 15_000


class AsyncChromiumSession:
    """Owns Playwright resources; it does not navigate or enable downloads."""

    def __init__(self, options: BrowserLaunchOptions | None = None) -> None:
        self.options = options or BrowserLaunchOptions()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: PageLike | None = None

    @property
    def page(self) -> PageLike:
        if self._page is None:
            raise DicConfigurationError("Chromium session has not been started")
        return self._page

    async def start(self, storage_state: dict[str, JsonValue] | None = None) -> PageLike:
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise DicConfigurationError("Playwright is not installed") from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.options.headless)
        context_options: dict[str, Any] = {
            "accept_downloads": False,
            "locale": self.options.locale,
            "service_workers": "block",
            "timezone_id": self.options.timezone_id,
        }
        if storage_state is not None:
            context_options["storage_state"] = storage_state
        self._context = await self._browser.new_context(**context_options)
        playwright_page = await self._context.new_page()
        playwright_page.set_default_navigation_timeout(self.options.navigation_timeout_ms)
        playwright_page.set_default_timeout(self.options.navigation_timeout_ms)
        self._page = cast(PageLike, playwright_page)
        return self._page

    async def storage_state(self) -> dict[str, JsonValue]:
        if self._context is None:
            raise DicConfigurationError("Chromium session has not been started")
        return cast(dict[str, JsonValue], await self._context.storage_state())

    async def close(self) -> None:
        self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
