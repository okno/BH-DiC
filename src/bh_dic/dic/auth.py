"""Deterministic login flow and encrypted browser-session lifecycle."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import JsonValue

from bh_dic.dic.errors import (
    DicAuthenticationError,
    DicAuthorizationError,
    DicCaptchaRequiredError,
    DicConfigurationError,
    DicMfaRequiredError,
    DicSessionExpiredError,
)
from bh_dic.dic.models import (
    DicCredentials,
    SessionState,
    SessionStatus,
    StoredBrowserSession,
)
from bh_dic.dic.pages import LoginPage, PageLike
from bh_dic.dic.session_vault import FernetSessionVault


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            expected_tenant_id is not None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", expected_tenant_id) is None
        ):
            raise DicConfigurationError("expected_tenant_id has an invalid format")
        self.page = page
        self.login_page = LoginPage(page, base_url)
        self.expected_tenant_id = expected_tenant_id
        self._clock = clock
        self._authenticated_at: datetime | None = None

    async def status(self) -> SessionStatus:
        marker = await self.login_page.locate("auth.authenticated", required=False)
        authenticated = marker is not None and await marker.is_visible()
        if authenticated:
            await self._verify_tenant()
        return SessionStatus(
            state=SessionState.AUTHENTICATED if authenticated else SessionState.UNKNOWN,
            authenticated_at=self._authenticated_at,
        )

    async def _verify_tenant(self) -> None:
        if self.expected_tenant_id is None:
            raise DicConfigurationError("expected DIC tenant is not configured")
        tenant = await self.login_page.locate("auth.tenant", required=False)
        if tenant is None:
            raise DicAuthorizationError("authenticated DIC tenant cannot be verified")
        observed = (
            await tenant.get_attribute("data-current-tenant-id")
            or await tenant.get_attribute("data-tenant-id")
            or await tenant.get_attribute("data-company-id")
            or (await tenant.inner_text()).strip()
        )
        if observed != self.expected_tenant_id:
            raise DicAuthorizationError("authenticated DIC tenant does not match configuration")

    async def authenticate(self, credentials: DicCredentials) -> SessionStatus:
        await self.login_page.open()
        captcha = await self.login_page.locate("auth.captcha", required=False)
        if captcha is not None and await captcha.is_visible():
            raise DicCaptchaRequiredError("DIC login requires human CAPTCHA verification")
        await self.login_page.fill("auth.username", credentials.username)
        await self.login_page.fill("auth.password", credentials.password.get_secret_value())
        await self.login_page.click("auth.submit")
        await self.page.wait_for_load_state("domcontentloaded", timeout=self.login_page.timeout_ms)
        mfa = await self.login_page.locate("auth.mfa", required=False)
        if mfa is not None and await mfa.is_visible():
            if credentials.totp is None:
                raise DicMfaRequiredError("DIC login requires a one-time code")
            await mfa.fill(credentials.totp.get_secret_value())
            await self.login_page.click("auth.submit")
            await self.page.wait_for_load_state(
                "domcontentloaded", timeout=self.login_page.timeout_ms
            )
        marker = await self.login_page.locate("auth.authenticated", required=False)
        if marker is None or not await marker.is_visible():
            raise DicAuthenticationError("DIC login did not reach an authenticated route")
        await self._verify_tenant()
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
