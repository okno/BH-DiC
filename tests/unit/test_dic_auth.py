from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from bh_dic.dic.auth import PlaywrightAuthenticator
from bh_dic.dic.errors import (
    DicAuthenticationError,
    DicAuthorizationError,
    DicCaptchaRequiredError,
    DicConfigurationError,
    DicMfaRequiredError,
)
from bh_dic.dic.models import DicCredentials, SessionState


class AuthLocator:
    def __init__(self, *, present: bool, tenant_id: str | None = None) -> None:
        self.present = present
        self.tenant_id = tenant_id

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return int(self.present)

    async def is_visible(self) -> bool:
        return self.present

    async def get_attribute(self, name: str) -> str | None:
        if name in {"data-current-tenant-id", "data-tenant-id", "data-company-id"}:
            return self.tenant_id
        return None

    async def inner_text(self) -> str:
        return self.tenant_id or ""


class AuthPage:
    def __init__(self, tenant_id: str | None) -> None:
        self.tenant_id = tenant_id

    def get_by_role(self, role, *, name=None, exact=None):
        del name, exact
        return AuthLocator(present=role == "navigation")

    def get_by_test_id(self, test_id):
        if test_id == "current-tenant":
            return AuthLocator(present=self.tenant_id is not None, tenant_id=self.tenant_id)
        return AuthLocator(present=False)

    def locator(self, selector):
        del selector
        return AuthLocator(present=False)


@pytest.mark.asyncio
async def test_authenticated_session_must_match_configured_tenant() -> None:
    authenticator = PlaywrightAuthenticator(
        AuthPage("TENANT-SYNTH-001"),  # type: ignore[arg-type]
        "https://secure.dipendentincloud.it",
        expected_tenant_id="TENANT-SYNTH-001",
    )
    assert (await authenticator.status()).state.value == "authenticated"


@pytest.mark.asyncio
async def test_authenticated_session_rejects_different_tenant() -> None:
    authenticator = PlaywrightAuthenticator(
        AuthPage("TENANT-OTHER"),  # type: ignore[arg-type]
        "https://secure.dipendentincloud.it",
        expected_tenant_id="TENANT-SYNTH-001",
    )
    with pytest.raises(DicAuthorizationError, match="does not match"):
        await authenticator.status()


class FlowLocator:
    def __init__(
        self,
        *,
        present: bool = False,
        visible: bool = True,
        tenant_id: str | None = None,
        tenant_in_attributes: bool = True,
    ) -> None:
        self.present = present
        self.visible = visible
        self.tenant_id = tenant_id
        self.tenant_in_attributes = tenant_in_attributes
        self.filled: list[str] = []
        self.clicks = 0

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return int(self.present)

    async def is_visible(self) -> bool:
        return self.visible

    async def get_attribute(self, name: str) -> str | None:
        if self.tenant_in_attributes and name == "data-current-tenant-id":
            return self.tenant_id
        return None

    async def inner_text(self) -> str:
        return self.tenant_id or ""

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def click(self) -> None:
        self.clicks += 1


class AuthFlowPage:
    def __init__(
        self,
        *,
        authenticated: bool = False,
        tenant_id: str | None = "TENANT-SYNTH-001",
        tenant_in_attributes: bool = True,
        captcha: bool = False,
        mfa: bool = False,
    ) -> None:
        self._url = ""
        self.authenticated = FlowLocator(present=authenticated)
        self.tenant = FlowLocator(
            present=tenant_id is not None,
            tenant_id=tenant_id,
            tenant_in_attributes=tenant_in_attributes,
        )
        self.captcha = FlowLocator(present=captcha)
        self.mfa = FlowLocator(present=mfa)
        self.username = FlowLocator(present=True)
        self.password = FlowLocator(present=True)
        self.submit = FlowLocator(present=True)
        self.waits = 0

    @property
    def url(self) -> str:
        return self._url

    def get_by_role(self, role, *, name=None, exact=None):
        del exact
        if role == "navigation":
            return self.authenticated
        if role == "button" and name in {"Accedi", "Login"}:
            return self.submit
        return FlowLocator()

    def get_by_label(self, text, *, exact=None):
        del exact
        if text in {"Email", "Username"}:
            return self.username
        if text == "Password":
            return self.password
        if text in {"Codice di verifica", "Codice OTP"}:
            return self.mfa
        return FlowLocator()

    def get_by_test_id(self, test_id):
        return {
            "captcha": self.captcha,
            "current-tenant": self.tenant,
            "app-sidebar": self.authenticated,
        }.get(test_id, FlowLocator())

    def locator(self, selector):
        del selector
        return FlowLocator()

    async def goto(
        self,
        url: str,
        *,
        wait_until=None,
        timeout=None,  # noqa: ASYNC109
    ) -> object:
        del wait_until, timeout
        self._url = url
        return object()

    async def wait_for_load_state(
        self,
        state=None,
        *,
        timeout=None,  # noqa: ASYNC109
    ) -> None:
        del state, timeout
        self.waits += 1


def credentials(*, totp: str | None = None) -> DicCredentials:
    return DicCredentials(
        username="synthetic@example.invalid",
        password=SecretStr("synthetic-password"),
        totp=SecretStr(totp) if totp is not None else None,
    )


def authenticator(page: AuthFlowPage, *, tenant_id: str | None = "TENANT-SYNTH-001"):
    return PlaywrightAuthenticator(  # type: ignore[arg-type]
        page,
        "https://secure.dipendentincloud.it",
        expected_tenant_id=tenant_id,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_authenticator_rejects_invalid_expected_tenant_id() -> None:
    with pytest.raises(DicConfigurationError, match="invalid format"):
        authenticator(AuthFlowPage(), tenant_id="tenant with spaces")


@pytest.mark.asyncio
async def test_session_status_is_unknown_without_marker_and_fails_closed_without_tenant() -> None:
    assert (await authenticator(AuthFlowPage()).status()).state is SessionState.UNKNOWN
    with pytest.raises(DicConfigurationError, match="not configured"):
        await authenticator(AuthFlowPage(authenticated=True), tenant_id=None).status()
    with pytest.raises(DicAuthorizationError, match="cannot be verified"):
        await authenticator(
            AuthFlowPage(authenticated=True, tenant_id=None), tenant_id="EXPECTED"
        ).status()


@pytest.mark.asyncio
async def test_login_stops_for_captcha_mfa_and_missing_authenticated_marker() -> None:
    with pytest.raises(DicCaptchaRequiredError, match="CAPTCHA"):
        await authenticator(AuthFlowPage(captcha=True)).authenticate(credentials())

    with pytest.raises(DicMfaRequiredError, match="one-time code"):
        await authenticator(AuthFlowPage(mfa=True)).authenticate(credentials())

    with pytest.raises(DicAuthenticationError, match="authenticated route"):
        await authenticator(AuthFlowPage()).authenticate(credentials())


@pytest.mark.asyncio
async def test_login_with_mfa_fills_secrets_only_into_controls_and_verifies_tenant_text() -> None:
    page = AuthFlowPage(
        authenticated=True,
        mfa=True,
        tenant_id="TENANT-SYNTH-001",
        tenant_in_attributes=False,
    )
    result = await authenticator(page).authenticate(credentials(totp="123456"))

    assert result.state is SessionState.AUTHENTICATED
    assert result.authenticated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert page.username.filled == ["synthetic@example.invalid"]
    assert page.password.filled == ["synthetic-password"]
    assert page.mfa.filled == ["123456"]
    assert page.submit.clicks == 2
    assert page.waits == 3
