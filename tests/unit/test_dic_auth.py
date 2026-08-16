from __future__ import annotations

from collections.abc import Callable
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
    DicPasswordExpiredError,
)
from bh_dic.dic.models import DicCredentials, SessionState

EXPECTED_TENANT = "123456789"
DIC_ORIGIN = "https://secure.dipendentincloud.it"
IDENTITY_ORIGIN = "https://identity.teamsystem.com"


class FakeRequest:
    method = "GET"


class FakeResponse:
    url = f"{DIC_ORIGIN}/backend_apiV2/company/info"
    status = 200
    request = FakeRequest()

    async def header_value(self, name: str) -> str | None:
        return {
            "content-type": "application/json; charset=utf-8",
            "content-length": "57",
        }.get(name.casefold())

    async def body(self) -> bytes:
        return b'{"data":{"company":{"id":' + EXPECTED_TENANT.encode() + b"}}}"


class FlowLocator:
    def __init__(
        self,
        *,
        present: Callable[[], bool] = lambda: False,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self._present = present
        self._on_click = on_click
        self.filled: list[str] = []
        self.clicks = 0

    @property
    def first(self) -> FlowLocator:
        return self

    async def count(self) -> int:
        return int(self._present())

    async def is_visible(self) -> bool:
        return self._present()

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def click(self) -> None:
        self.clicks += 1
        if self._on_click is not None:
            self._on_click()


class AuthFlowPage:
    def __init__(
        self,
        *,
        authenticated: bool = False,
        captcha: bool = False,
        session_result: str = "login",
        email_result: str = "password",
        identity_result: str = "authenticated",
        response: FakeResponse | None = None,
        stale_response: FakeResponse | None = None,
    ) -> None:
        self._url = "about:blank"
        self.authenticated = authenticated
        self.captcha_enabled = captcha
        self.session_result = session_result
        self.email_result = email_result
        self.identity_result = identity_result
        self.response = response or FakeResponse()
        self.stale_response = stale_response
        self.handlers: list[Callable[[FakeResponse], None]] = []
        self.goto_urls: list[str] = []
        self.waits = 0
        self.dic_email = FlowLocator(
            present=lambda: self._url == f"{DIC_ORIGIN}/it/login",
        )
        self.dic_submit = FlowLocator(
            present=lambda: self._url == f"{DIC_ORIGIN}/it/login",
            on_click=lambda: self._set_url(f"{IDENTITY_ORIGIN}/Account/LoginEmail?flow=x"),
        )
        self.identity_email = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginEmail"),
        )
        self.identity_email_submit = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginEmail"),
            on_click=self._finish_email,
        )
        self.identity_password = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginPassword"),
        )
        self.identity_password_submit = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginPassword"),
            on_click=self._finish_password,
        )
        self.captcha = FlowLocator(present=lambda: self.captcha_enabled)
        self.mfa = FlowLocator(
            present=lambda: (
                self.identity_result == "mfa" and self._identity_path("/Account/LoginPassword")
            )
        )
        self.marker = FlowLocator(
            present=lambda: (
                self.authenticated and self._url == f"{DIC_ORIGIN}/it/app/employees/list"
            )
        )
        self.missing = FlowLocator()

    @property
    def url(self) -> str:
        return self._url

    def _identity_path(self, path: str) -> bool:
        return self._url.startswith(f"{IDENTITY_ORIGIN}{path}")

    def _set_url(self, url: str) -> None:
        self._url = url

    def _finish_password(self) -> None:
        if self.identity_result == "authenticated":
            self.authenticated = True
            self._url = f"{DIC_ORIGIN}/it/app/employees"
        elif self.identity_result == "expired":
            self._url = f"{IDENTITY_ORIGIN}/Account/LoginPasswordExpired?flow=x"
        elif self.identity_result == "wrong-origin":
            self._url = "https://identity.teamsystem.com.evil.invalid/Account/LoginPassword"

    def _finish_email(self) -> None:
        if self.email_result == "password":
            self._url = f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=x"
        elif self.email_result == "passwordless":
            self.authenticated = True
            self._url = f"{DIC_ORIGIN}/it/app/employees"

    def on(self, event: str, handler: Callable[[FakeResponse], None]) -> None:
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event: str, handler: Callable[[FakeResponse], None]) -> None:
        assert event == "response"
        self.handlers.remove(handler)

    def get_by_role(self, role: str, *, name=None, exact=None):
        del role, name, exact
        return self.missing

    def get_by_label(self, text: str, *, exact=None):
        del text, exact
        return self.missing

    def get_by_placeholder(self, text: str):
        if text == "Inserisci la tua e-mail":
            return self.dic_email
        return self.missing

    def get_by_test_id(self, test_id: str):
        if test_id == "login-submit":
            return self.dic_submit
        if test_id == "captcha":
            return self.captcha
        return self.missing

    def locator(self, selector: str):
        return {
            "#EmailAddress_Email": self.identity_email,
            "#submitEmailBtn": self.identity_email_submit,
            "#selectPassword": self.identity_password,
            "#submitBtn": self.identity_password_submit,
            "dic-navbar": self.marker,
            "input[autocomplete='one-time-code']": self.mfa,
        }.get(selector, self.missing)

    async def goto(self, url: str, *, wait_until=None, timeout=None) -> object:  # noqa: ASYNC109
        del wait_until, timeout
        self.goto_urls.append(url)
        if url == "about:blank":
            self._url = url
            if self.stale_response is not None:
                for handler in tuple(self.handlers):
                    handler(self.stale_response)
                self.stale_response = None
        elif url == f"{DIC_ORIGIN}/it/app/employees/list":
            if self.authenticated:
                self._url = url
                for handler in tuple(self.handlers):
                    handler(self.response)
            else:
                if self.session_result == "expired":
                    self._url = f"{IDENTITY_ORIGIN}/Account/LoginPasswordExpired?flow=session"
                else:
                    self._url = f"{IDENTITY_ORIGIN}/Account/LoginEmail?flow=session"
        elif url == f"{DIC_ORIGIN}/it/login":
            self._url = url
        else:
            raise AssertionError(f"unexpected synthetic navigation: {url}")
        return object()

    async def wait_for_load_state(self, state=None, *, timeout=None) -> None:  # noqa: ASYNC109
        del state, timeout
        self.waits += 1


def credentials(*, totp: str | None = None) -> DicCredentials:
    return DicCredentials(
        username="synthetic@example.invalid",
        password=SecretStr("synthetic-password"),
        totp=SecretStr(totp) if totp is not None else None,
    )


def authenticator(
    page: AuthFlowPage,
    *,
    tenant_id: str | None = EXPECTED_TENANT,
) -> PlaywrightAuthenticator:
    return PlaywrightAuthenticator(  # type: ignore[arg-type]
        page,
        DIC_ORIGIN,
        expected_tenant_id=tenant_id,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_restored_session_is_probed_from_about_blank_and_attested() -> None:
    page = AuthFlowPage(authenticated=True)

    result = await authenticator(page).status()

    assert result.state is SessionState.AUTHENTICATED
    assert page.goto_urls == ["about:blank", f"{DIC_ORIGIN}/it/app/employees/list"]
    assert page.handlers == []


@pytest.mark.asyncio
async def test_session_status_is_unknown_after_exact_identity_redirect() -> None:
    page = AuthFlowPage(authenticated=False)

    assert (await authenticator(page).status()).state is SessionState.UNKNOWN
    assert page.handlers == []


@pytest.mark.asyncio
async def test_session_probe_reports_password_expired_without_route_details() -> None:
    page = AuthFlowPage(session_result="expired")

    with pytest.raises(DicPasswordExpiredError) as caught:
        await authenticator(page).status()

    assert str(caught.value) == "DIC password renewal is required"
    assert page.handlers == []


@pytest.mark.asyncio
async def test_authenticated_session_rejects_different_tenant() -> None:
    class OtherTenantResponse(FakeResponse):
        async def body(self) -> bytes:
            return b'{"data":{"company":{"id":987654321}}}'

    with pytest.raises(DicAuthorizationError, match="does not match"):
        await authenticator(
            AuthFlowPage(authenticated=True, response=OtherTenantResponse())
        ).status()


@pytest.mark.asyncio
async def test_stale_pre_navigation_response_cannot_attest_new_probe() -> None:
    class OtherTenantResponse(FakeResponse):
        async def body(self) -> bytes:
            return b'{"data":{"company":{"id":987654321}}}'

    page = AuthFlowPage(
        authenticated=True,
        stale_response=FakeResponse(),
        response=OtherTenantResponse(),
    )

    with pytest.raises(DicAuthorizationError, match="does not match"):
        await authenticator(page).status()

    assert page.stale_response is None
    assert page.handlers == []


def test_authenticator_rejects_malformed_configured_tenant_id() -> None:
    for value in ("tenant with spaces", "", "-tenant", "TENANT-SYNTH-001", "0", "0123"):
        with pytest.raises(DicConfigurationError, match="invalid format"):
            authenticator(AuthFlowPage(), tenant_id=value)


def test_authenticator_applies_and_bounds_configured_login_timeout() -> None:
    configured = PlaywrightAuthenticator(  # type: ignore[arg-type]
        AuthFlowPage(),
        DIC_ORIGIN,
        expected_tenant_id=EXPECTED_TENANT,
        login_timeout_ms=60_000,
    )
    assert configured.login_page.timeout_ms == 60_000
    assert configured.employees_page.timeout_ms == 60_000

    for timeout_ms in (4_999, 300_001):
        with pytest.raises(DicConfigurationError, match="login_timeout_ms"):
            PlaywrightAuthenticator(  # type: ignore[arg-type]
                AuthFlowPage(),
                DIC_ORIGIN,
                expected_tenant_id=EXPECTED_TENANT,
                login_timeout_ms=timeout_ms,
            )


@pytest.mark.asyncio
async def test_status_fails_before_navigation_without_expected_tenant() -> None:
    page = AuthFlowPage(authenticated=True)

    with pytest.raises(DicConfigurationError, match="not configured"):
        await authenticator(page, tenant_id=None).status()

    assert page.goto_urls == []


@pytest.mark.asyncio
async def test_exact_dic_and_teamsystem_login_flow_attests_tenant() -> None:
    page = AuthFlowPage()

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert result.authenticated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert page.dic_email.filled == ["synthetic@example.invalid"]
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_password.filled == ["synthetic-password"]
    assert page.dic_submit.clicks == 1
    assert page.identity_email_submit.clicks == 1
    assert page.identity_password_submit.clicks == 1
    assert page.goto_urls == [
        f"{DIC_ORIGIN}/it/login",
        "about:blank",
        f"{DIC_ORIGIN}/it/app/employees/list",
    ]


@pytest.mark.asyncio
async def test_login_stops_for_captcha_before_sending_credentials() -> None:
    page = AuthFlowPage(captcha=True)

    with pytest.raises(DicCaptchaRequiredError, match="CAPTCHA"):
        await authenticator(page).authenticate(credentials())

    assert page.dic_email.filled == []
    assert page.identity_email.filled == []
    assert page.identity_password.filled == []


@pytest.mark.asyncio
async def test_password_expired_has_dedicated_generic_error() -> None:
    page = AuthFlowPage(identity_result="expired")

    with pytest.raises(DicPasswordExpiredError) as caught:
        await authenticator(page).authenticate(credentials())

    assert str(caught.value) == "DIC password renewal is required"
    assert "Account/LoginPasswordExpired" not in str(caught.value)


@pytest.mark.asyncio
async def test_login_rejects_lookalike_identity_origin() -> None:
    page = AuthFlowPage(identity_result="wrong-origin")

    with pytest.raises(DicAuthenticationError, match="unexpected identity route"):
        await authenticator(page).authenticate(credentials())


@pytest.mark.asyncio
async def test_login_never_accepts_passwordless_identity_completion() -> None:
    page = AuthFlowPage(email_result="passwordless")

    with pytest.raises(DicAuthenticationError, match="unexpected identity route"):
        await authenticator(page).authenticate(credentials())

    assert page.identity_password.filled == []


@pytest.mark.asyncio
async def test_mfa_fails_closed_even_when_totp_is_configured() -> None:
    page = AuthFlowPage(identity_result="mfa")

    with pytest.raises(DicMfaRequiredError, match="interactive MFA"):
        await authenticator(page).authenticate(credentials(totp="123456"))

    assert page.mfa.filled == []
    assert page.identity_password_submit.clicks == 1
