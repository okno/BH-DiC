from __future__ import annotations

import builtins
import sys
from types import ModuleType

import pytest

from bh_dic.dic.browser import AsyncChromiumSession, BrowserLaunchOptions
from bh_dic.dic.errors import DicConfigurationError


class FakePlaywrightPage:
    def __init__(self) -> None:
        self.navigation_timeout: float | None = None
        self.default_timeout: float | None = None

    def set_default_navigation_timeout(self, value: float) -> None:
        self.navigation_timeout = value

    def set_default_timeout(self, value: float) -> None:
        self.default_timeout = value


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePlaywrightPage()
        self.closed = False

    async def new_page(self) -> FakePlaywrightPage:
        return self.page

    async def storage_state(self):
        return {"cookies": [], "origins": []}

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.context_options = None
        self.closed = False

    async def new_context(self, **options):
        self.context_options = options
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.headless: bool | None = None

    async def launch(self, *, headless: bool):
        self.headless = headless
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


@pytest.mark.asyncio
async def test_chromium_lifecycle_uses_hardened_context_options(monkeypatch) -> None:
    fake = FakePlaywright()
    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(fake)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    options = BrowserLaunchOptions(
        headless=False,
        locale="it-IT",
        timezone_id="Europe/Zurich",
        navigation_timeout_ms=1234,
    )
    session = AsyncChromiumSession(options)
    with pytest.raises(DicConfigurationError, match="not been started"):
        _ = session.page
    with pytest.raises(DicConfigurationError, match="not been started"):
        await session.storage_state()

    storage_state = {"cookies": [], "origins": []}
    page = await session.start(storage_state)
    assert await session.start() is page
    assert fake.chromium.headless is False
    assert fake.chromium.browser.context_options == {
        "accept_downloads": False,
        "locale": "it-IT",
        "service_workers": "block",
        "timezone_id": "Europe/Zurich",
        "storage_state": storage_state,
    }
    assert fake.chromium.browser.context.page.navigation_timeout == 1234
    assert fake.chromium.browser.context.page.default_timeout == 1234
    assert await session.storage_state() == storage_state

    await session.close()
    await session.close()
    assert fake.chromium.browser.context.closed is True
    assert fake.chromium.browser.closed is True
    assert fake.stopped is True


@pytest.mark.asyncio
async def test_chromium_start_reports_missing_playwright(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "playwright.async_api":
            raise ImportError("synthetic missing dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(DicConfigurationError, match="not installed"):
        await AsyncChromiumSession().start()
