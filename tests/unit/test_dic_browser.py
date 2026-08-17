from __future__ import annotations

import asyncio
import builtins
import sys
from collections.abc import Awaitable, Callable
from types import ModuleType
from urllib.parse import urlsplit

import pytest

from bh_dic.dic.browser import AsyncChromiumSession, BrowserLaunchOptions
from bh_dic.dic.errors import DicConfigurationError, DicSessionVaultError
from bh_dic.dic.models import StoredDicSessionStorage, StoredSessionStorageEntry


class FakePlaywrightPage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.navigation_timeout: float | None = None
        self.default_timeout: float | None = None
        self.url = "about:blank"
        self.session_storage: dict[str, str] = {}
        self.restore_calls = 0
        self.evaluate_error: BaseException | None = None
        self.evaluated_result: object = {
            "origin": "https://secure.dipendentincloud.it",
            "entries": [],
        }

    def set_default_navigation_timeout(self, value: float) -> None:
        self.navigation_timeout = value

    def set_default_timeout(self, value: float) -> None:
        self.default_timeout = value

    async def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> object:
        del wait_until, timeout
        self.context.events.append(f"goto:{url}")
        handler = self.context.routes.get(url)
        if handler is not None:
            route = FakeRoute(self.context)
            await handler(route)
            assert route.fulfilled is True
        self.url = url
        return object()

    async def evaluate(self, _expression: str, argument: object = None) -> object:
        if self.evaluate_error is not None:
            raise self.evaluate_error
        if argument is not None:
            self.restore_calls += 1
            self.context.events.append("evaluate_restore")
            payload = argument if isinstance(argument, dict) else {}
            origin = urlsplit(self.url)
            current_origin = f"{origin.scheme}://{origin.netloc}"
            if (
                current_origin != "https://secure.dipendentincloud.it"
                or payload.get("origin") != current_origin
            ):
                return False
            entries = payload.get("entries")
            assert isinstance(entries, list)
            for entry in entries:
                assert isinstance(entry, dict)
                self.session_storage[str(entry["key"])] = str(entry["value"])
            return True
        self.context.events.append("evaluate_export")
        return self.evaluated_result


class FakeRoute:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.fulfilled = False

    async def fulfill(
        self,
        *,
        status: int,
        content_type: str,
        body: str,
    ) -> None:
        assert status == 200
        assert content_type == "text/html"
        assert body.startswith("<!doctype html>")
        self.fulfilled = True
        self.context.events.append("fulfill_bootstrap")


class FakeContext:
    def __init__(self) -> None:
        self.closed = False
        self.events: list[str] = []
        self.routes: dict[str, Callable[[FakeRoute], Awaitable[None]]] = {}
        self.page = FakePlaywrightPage(self)

    async def new_page(self) -> FakePlaywrightPage:
        self.events.append("new_page")
        return self.page

    async def route(
        self,
        url: str,
        handler: Callable[[FakeRoute], Awaitable[None]],
    ) -> None:
        self.events.append(f"route:{url}")
        self.routes[url] = handler

    async def unroute(
        self,
        url: str,
        handler: Callable[[FakeRoute], Awaitable[None]],
    ) -> None:
        self.events.append(f"unroute:{url}")
        assert self.routes[url] is handler
        del self.routes[url]

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
    with pytest.raises(DicConfigurationError, match="not been started"):
        await session.dic_session_storage()

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
    assert await session.dic_session_storage() == StoredDicSessionStorage(
        origin="https://secure.dipendentincloud.it", entries=()
    )

    await session.close()
    await session.close()
    assert fake.chromium.browser.context.closed is True
    assert fake.chromium.browser.closed is True
    assert fake.stopped is True


@pytest.mark.asyncio
async def test_chromium_restores_session_storage_once_before_dic_app_navigation(
    monkeypatch,
) -> None:
    fake = FakePlaywright()
    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(fake)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    marker = "PRIVATE-SESSION-STORAGE-MARKER"
    restored = StoredDicSessionStorage(
        origin="https://secure.dipendentincloud.it",
        entries=(StoredSessionStorageEntry(key="TSID", value=marker),),
    )

    await AsyncChromiumSession().start(session_storage=restored)

    context = fake.chromium.browser.context
    page = context.page
    bootstrap_url = "https://secure.dipendentincloud.it/__bh_dic_session_bootstrap__"
    assert context.events == [
        "new_page",
        f"route:{bootstrap_url}",
        f"goto:{bootstrap_url}",
        "fulfill_bootstrap",
        "evaluate_restore",
        f"unroute:{bootstrap_url}",
    ]
    assert context.routes == {}
    assert page.session_storage == {"TSID": marker}
    assert page.restore_calls == 1

    page.session_storage["TSID"] = "REFRESHED-TOKEN"
    await page.goto("https://secure.dipendentincloud.it/it/app/companies/dic")
    assert page.session_storage == {"TSID": "REFRESHED-TOKEN"}
    assert page.restore_calls == 1

    page.session_storage.clear()
    await page.goto("https://secure.dipendentincloud.it/it/login")
    assert page.session_storage == {}
    assert page.restore_calls == 1


@pytest.mark.asyncio
async def test_chromium_session_storage_export_rejects_wrong_origin_and_oversize_without_leak(
    monkeypatch,
) -> None:
    fake = FakePlaywright()
    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(fake)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    session = AsyncChromiumSession()
    await session.start()
    page = fake.chromium.browser.context.page

    page.evaluated_result = None
    with pytest.raises(DicSessionVaultError, match="unavailable or invalid") as wrong_origin:
        await session.dic_session_storage()
    assert "origin" not in str(wrong_origin.value).lower()

    marker = "PRIVATE-OVERSIZE-MARKER"
    page.evaluated_result = {
        "origin": "https://secure.dipendentincloud.it",
        "entries": [{"key": "TSID", "value": marker + ("x" * 32_768)}],
    }
    with pytest.raises(DicSessionVaultError, match="unavailable or invalid") as oversize:
        await session.dic_session_storage()
    assert marker not in str(oversize.value)


@pytest.mark.asyncio
async def test_session_storage_capture_sanitizes_provider_error_and_preserves_cancellation(
    monkeypatch,
) -> None:
    fake = FakePlaywright()
    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(fake)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    session = AsyncChromiumSession()
    await session.start()
    marker = "PRIVATE-CAPTURE-EXCEPTION-MARKER"

    fake.chromium.browser.context.page.evaluate_error = RuntimeError(marker)
    with pytest.raises(DicSessionVaultError, match="exported safely") as caught:
        await session.dic_session_storage()
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    cancellation = asyncio.CancelledError()
    fake.chromium.browser.context.page.evaluate_error = cancellation
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await session.dic_session_storage()
    assert cancelled.value is cancellation


@pytest.mark.asyncio
async def test_session_storage_restore_sanitizes_failure_unroutes_and_preserves_cancellation(
    monkeypatch,
) -> None:
    marker = "PRIVATE-RESTORE-EXCEPTION-MARKER"
    restored = StoredDicSessionStorage(
        origin="https://secure.dipendentincloud.it",
        entries=(StoredSessionStorageEntry(key="TSID", value=marker),),
    )

    failed = FakePlaywright()
    failed.chromium.browser.context.page.evaluate_error = RuntimeError(marker)
    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(failed)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    with pytest.raises(DicSessionVaultError, match="restored safely") as caught:
        await AsyncChromiumSession().start(session_storage=restored)
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert failed.chromium.browser.context.routes == {}

    cancelled = FakePlaywright()
    cancellation = asyncio.CancelledError()
    cancelled.chromium.browser.context.page.evaluate_error = cancellation
    module.async_playwright = lambda: FakeManager(cancelled)  # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError) as caught_cancelled:
        await AsyncChromiumSession().start(session_storage=restored)
    assert caught_cancelled.value is cancellation
    assert cancelled.chromium.browser.context.routes == {}


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
