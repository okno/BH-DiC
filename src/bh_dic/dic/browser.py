"""Minimal hardened async Playwright Chromium lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue, ValidationError

from bh_dic.dic.errors import DicConfigurationError, DicSessionVaultError
from bh_dic.dic.models import StoredDicSessionStorage
from bh_dic.dic.pages import PageLike

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route


_DIC_ORIGIN = "https://secure.dipendentincloud.it"
_SESSION_STORAGE_BOOTSTRAP_URL = f"{_DIC_ORIGIN}/__bh_dic_session_bootstrap__"
_SESSION_STORAGE_BOOTSTRAP_HTML = "<!doctype html><meta charset=utf-8><title></title>"
_SESSION_STORAGE_EXPORT_SCRIPT = """
() => {
  const trustedOrigin = "https://secure.dipendentincloud.it";
  if (globalThis.location.origin !== trustedOrigin) {
    return null;
  }
  if (globalThis.sessionStorage.length > 64) {
    return null;
  }
  const encoder = new TextEncoder();
  let totalBytes = 0;
  const entries = [];
  for (let index = 0; index < globalThis.sessionStorage.length; index += 1) {
    const key = globalThis.sessionStorage.key(index);
    if (key === null) {
      continue;
    }
    const value = globalThis.sessionStorage.getItem(key);
    if (value !== null) {
      const keyBytes = encoder.encode(key).byteLength;
      const valueBytes = encoder.encode(value).byteLength;
      totalBytes += keyBytes + valueBytes;
      if (keyBytes > 256 || valueBytes > 32768 || totalBytes > 131072) {
        return null;
      }
      entries.push({key, value});
    }
  }
  return {origin: trustedOrigin, entries};
}
"""
_SESSION_STORAGE_RESTORE_SCRIPT = """
(snapshot) => {
  const trustedOrigin = "https://secure.dipendentincloud.it";
  if (globalThis.location.origin !== trustedOrigin || snapshot.origin !== trustedOrigin) {
    return false;
  }
  for (const entry of snapshot.entries) {
    globalThis.sessionStorage.setItem(entry.key, entry.value);
  }
  return true;
}
"""


@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    headless: bool = True
    locale: str = "it-IT"
    timezone_id: str = "Europe/Zurich"
    navigation_timeout_ms: float = 15_000


class AsyncChromiumSession:
    """Owns Playwright resources and an isolated one-shot session restore bootstrap."""

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

    async def start(
        self,
        storage_state: dict[str, JsonValue] | None = None,
        *,
        session_storage: StoredDicSessionStorage | None = None,
    ) -> PageLike:
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
        if session_storage is not None:
            await self._restore_dic_session_storage(
                self._context,
                playwright_page,
                session_storage,
            )
        self._page = cast(PageLike, playwright_page)
        return self._page

    async def storage_state(self) -> dict[str, JsonValue]:
        if self._context is None:
            raise DicConfigurationError("Chromium session has not been started")
        return cast(dict[str, JsonValue], await self._context.storage_state())

    async def dic_session_storage(self) -> StoredDicSessionStorage:
        """Export only the bounded sessionStorage state of the exact DIC origin."""

        if self._page is None:
            raise DicConfigurationError("Chromium session has not been started")
        invalid_state = False
        capture_failed = False
        try:
            raw_state = await self._page.evaluate(_SESSION_STORAGE_EXPORT_SCRIPT)
            result = StoredDicSessionStorage.model_validate(raw_state)
        except asyncio.CancelledError:
            raise
        except ValidationError:
            raw_state = None
            invalid_state = True
        except Exception:
            raw_state = None
            capture_failed = True
        if invalid_state:
            raise DicSessionVaultError("DIC sessionStorage is unavailable or invalid")
        if capture_failed:
            raise DicSessionVaultError("DIC sessionStorage could not be exported safely")
        return result

    @staticmethod
    async def _restore_dic_session_storage(
        context: BrowserContext,
        page: Page,
        session_storage: StoredDicSessionStorage,
    ) -> None:
        async def fulfill_bootstrap(route: Route) -> None:
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=_SESSION_STORAGE_BOOTSTRAP_HTML,
            )

        route_installed = False
        restore_failed = False
        restored = False
        cancellation: asyncio.CancelledError | None = None
        try:
            await context.route(_SESSION_STORAGE_BOOTSTRAP_URL, fulfill_bootstrap)
            route_installed = True
            await page.goto(
                _SESSION_STORAGE_BOOTSTRAP_URL,
                wait_until="domcontentloaded",
            )
            restored = (
                await page.evaluate(
                    _SESSION_STORAGE_RESTORE_SCRIPT,
                    session_storage.model_dump(mode="json"),
                )
                is True
            )
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            restore_failed = True

        if route_installed:
            try:
                await context.unroute(_SESSION_STORAGE_BOOTSTRAP_URL, fulfill_bootstrap)
            except asyncio.CancelledError as exc:
                if cancellation is None:
                    cancellation = exc
            except Exception:
                restore_failed = True

        if cancellation is not None:
            raise cancellation
        if restore_failed or not restored:
            raise DicSessionVaultError("DIC sessionStorage could not be restored safely")

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
