"""Deterministic login flow and encrypted browser-session lifecycle."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from bh_dic.dic.errors import (
    DicAuthenticationError,
    DicCaptchaRequiredError,
    DicConfigurationError,
    DicMfaRequiredError,
    DicPasswordExpiredError,
    DicSessionExpiredError,
)
from bh_dic.dic.models import (
    DicCredentials,
    SessionState,
    SessionStatus,
    StoredBrowserSession,
)
from bh_dic.dic.pages import EmployeesListPage, LoginPage, PageLike
from bh_dic.dic.session_vault import FernetSessionVault
from bh_dic.dic.tenant_attestation import ResponseEventSource, TenantResponseCapture

_DIC_ORIGIN = "https://secure.dipendentincloud.it"
_TEAMSYSTEM_ORIGIN = "https://identity.teamsystem.com"
_TEAMSYSTEM_EMAIL_PATH = "/Account/LoginEmail"
_TEAMSYSTEM_SECRET_ENTRY_PATH = "/Account/Login" + "Password"
_TEAMSYSTEM_SECRET_RENEWAL_PATH = _TEAMSYSTEM_SECRET_ENTRY_PATH + "Expired"
_CANONICAL_TENANT_ID = re.compile(r"[1-9][0-9]{0,18}")


class StorageStateProvider(Protocol):
    async def storage_state(self) -> dict[str, JsonValue]: ...


class PlaywrightAuthenticator:
    """Credentials are consumed only by form controls and never serialized or logged."""

    def __init__(
        self,
        page: PageLike,
        base_url: str,
        *,
        expected_tenant_id: str | None = None,
        login_timeout_ms: float = 15_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            expected_tenant_id is not None
            and _CANONICAL_TENANT_ID.fullmatch(expected_tenant_id) is None
        ):
            raise DicConfigurationError("expected_tenant_id has an invalid format")
        if not 5_000 <= login_timeout_ms <= 300_000:
            raise DicConfigurationError("login_timeout_ms must be between 5000 and 300000")
        self.page = page
        self.login_page = LoginPage(page, base_url, timeout_ms=login_timeout_ms)
        self.employees_page = EmployeesListPage(page, base_url, timeout_ms=login_timeout_ms)
        self.expected_tenant_id = expected_tenant_id
        self._clock = clock
        self._authenticated_at: datetime | None = None

    async def status(self) -> SessionStatus:
        authenticated = await self._probe_authenticated_session()
        return SessionStatus(
            state=SessionState.AUTHENTICATED if authenticated else SessionState.UNKNOWN,
            authenticated_at=self._authenticated_at,
        )

    @staticmethod
    def _origin_and_path(url: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(url)
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise DicAuthenticationError("authentication reached an invalid route") from None
        return f"{parsed.scheme}://{parsed.netloc}", parsed.path

    @classmethod
    def _is_exact_route(
        cls,
        url: str,
        origin: str,
        path: str,
        *,
        allow_query: bool = True,
    ) -> bool:
        observed_origin, observed_path = cls._origin_and_path(url)
        parsed = urlsplit(url)
        return (
            observed_origin == origin
            and observed_path == path
            and parsed.scheme == "https"
            and (allow_query or not parsed.query)
            and not parsed.fragment
        )

    @classmethod
    def _raise_if_password_expired(cls, url: str) -> None:
        if cls._is_exact_route(url, _TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_SECRET_RENEWAL_PATH):
            raise DicPasswordExpiredError("DIC password renewal is required")

    @classmethod
    def _is_dic_app_route(cls, url: str) -> bool:
        observed_origin, observed_path = cls._origin_and_path(url)
        parsed = urlsplit(url)
        return (
            observed_origin == _DIC_ORIGIN
            and observed_path.startswith("/it/app/")
            and parsed.scheme == "https"
            and not parsed.fragment
        )

    async def _probe_authenticated_session(self) -> bool:
        if self.expected_tenant_id is None:
            raise DicConfigurationError("expected DIC tenant is not configured")

        # Cancel and drain requests owned by the previous document before installing
        # the attestation listener. Otherwise a late company response could attest
        # the new fixed-route probe.
        try:
            await self.page.goto(
                "about:blank",
                wait_until="domcontentloaded",
                timeout=self.login_page.timeout_ms,
            )
            await self.page.wait_for_load_state(
                "domcontentloaded", timeout=self.login_page.timeout_ms
            )
            await asyncio.sleep(0)
        except Exception:
            raise DicAuthenticationError("DIC session probe reset failed") from None
        if self.page.url != "about:blank":
            raise DicAuthenticationError("DIC session probe reset reached an unexpected route")

        event_source = cast(ResponseEventSource, self.page)
        with TenantResponseCapture(event_source) as capture:
            try:
                await self.employees_page.navigate()
            except Exception:
                raise DicAuthenticationError("DIC session probe navigation failed") from None

            self._raise_if_password_expired(self.page.url)
            if self._is_exact_route(self.page.url, _DIC_ORIGIN, "/it/login") or any(
                self._is_exact_route(self.page.url, _TEAMSYSTEM_ORIGIN, path)
                for path in (_TEAMSYSTEM_EMAIL_PATH, _TEAMSYSTEM_SECRET_ENTRY_PATH)
            ):
                return False
            if not self._is_exact_route(
                self.page.url,
                _DIC_ORIGIN,
                self.employees_page.route_template,
                allow_query=False,
            ):
                raise DicAuthenticationError("DIC session probe reached an unexpected route")
            marker = await self.login_page.locate("auth.authenticated", required=False)
            if marker is None or not await marker.is_visible():
                return False
            await capture.attest(
                self.expected_tenant_id,
                timeout_ms=self.login_page.timeout_ms,
            )
        return True

    async def _captcha_guard(self) -> None:
        captcha = await self.login_page.locate("auth.captcha", required=False)
        if captcha is not None and await captcha.is_visible():
            raise DicCaptchaRequiredError("DIC login requires human CAPTCHA verification")

    async def _wait_for_navigation(self) -> None:
        await self.page.wait_for_load_state("domcontentloaded", timeout=self.login_page.timeout_ms)

    def _require_route(self, origin: str, path: str) -> None:
        self._raise_if_password_expired(self.page.url)
        if not self._is_exact_route(self.page.url, origin, path):
            raise DicAuthenticationError("DIC login reached an unexpected identity route")

    async def authenticate(self, credentials: DicCredentials) -> SessionStatus:
        if self.expected_tenant_id is None:
            raise DicConfigurationError("expected DIC tenant is not configured")
        try:
            await self.login_page.navigate()
        except Exception:
            raise DicAuthenticationError("DIC login navigation failed") from None
        self._raise_if_password_expired(self.page.url)

        if self._is_exact_route(self.page.url, _DIC_ORIGIN, "/it/login"):
            await self._captcha_guard()
            await self.login_page.fill("auth.dic_email", credentials.username)
            await self.login_page.click("auth.dic_submit")
            await self._wait_for_navigation()
        elif self._is_dic_app_route(self.page.url):
            if await self._probe_authenticated_session():
                self._authenticated_at = self._clock()
                return SessionStatus(
                    state=SessionState.AUTHENTICATED,
                    authenticated_at=self._authenticated_at,
                )

        self._require_route(_TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_EMAIL_PATH)
        await self._captcha_guard()
        await self.login_page.fill("auth.teamsystem_email", credentials.username)
        await self.login_page.click("auth.teamsystem_email_submit")
        await self._wait_for_navigation()

        self._require_route(_TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_SECRET_ENTRY_PATH)
        await self._captcha_guard()
        await self.login_page.fill(
            "auth.teamsystem_password", credentials.password.get_secret_value()
        )
        await self.login_page.click("auth.teamsystem_password_submit")
        await self._wait_for_navigation()
        self._raise_if_password_expired(self.page.url)

        mfa = await self.login_page.locate("auth.mfa", required=False)
        if mfa is not None and await mfa.is_visible():
            self._require_route(_TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_SECRET_ENTRY_PATH)
            raise DicMfaRequiredError("DIC login requires interactive MFA verification")
        elif not self._is_dic_app_route(self.page.url):
            raise DicAuthenticationError("DIC login reached an unexpected identity route")
        if not await self._probe_authenticated_session():
            raise DicAuthenticationError("DIC login did not reach an authenticated route")
        self._authenticated_at = self._clock()
        return SessionStatus(
            state=SessionState.AUTHENTICATED,
            authenticated_at=self._authenticated_at,
        )


class DicSessionManager:
    def __init__(
        self,
        vault: FernetSessionVault,
        *,
        lifetime: timedelta = timedelta(hours=8),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("session lifetime must be positive")
        self.vault = vault
        self.lifetime = lifetime
        self._clock = clock

    def load_storage_state(self) -> dict[str, JsonValue] | None:
        try:
            session = self.vault.load()
        except DicSessionExpiredError:
            return None
        return None if session is None else session.storage_state

    async def persist(
        self,
        provider: StorageStateProvider,
        *,
        account_hint_redacted: str | None = None,
    ) -> StoredBrowserSession:
        now = self._clock()
        session = StoredBrowserSession(
            storage_state=await provider.storage_state(),
            authenticated_at=now,
            expires_at=now + self.lifetime,
            account_hint_redacted=account_hint_redacted,
        )
        self.vault.save(session)
        return session

    def invalidate(self) -> None:
        self.vault.invalidate()
