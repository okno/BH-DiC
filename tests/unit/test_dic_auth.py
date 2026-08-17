from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from bh_dic.dic.auth import (
    DicAuthOutcomeUnknownError,
    DicAuthStage,
    DicAuthUiChangedError,
    PlaywrightAuthenticator,
)
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
        on_visible: Callable[[], None] | None = None,
        on_evaluate: Callable[[str, object], object] | None = None,
    ) -> None:
        self._present = present
        self._on_click = on_click
        self._on_visible = on_visible
        self._on_evaluate = on_evaluate
        self.filled: list[str] = []
        self.clicks = 0

    @property
    def first(self) -> FlowLocator:
        return self

    def nth(self, index: int) -> FlowLocator:
        if index != 0:
            raise IndexError(index)
        return self

    async def count(self) -> int:
        return int(self._present())

    async def is_visible(self) -> bool:
        if self._on_visible is not None:
            self._on_visible()
        return self._present()

    async def evaluate(self, expression: str, argument: object = None) -> object:
        if self._on_evaluate is not None:
            return self._on_evaluate(expression, argument)
        if isinstance(argument, dict) and "secret" in argument:
            await self.fill(str(argument["secret"]))
            return "CONTROL_OK"
        await self.click()
        return "CONTROL_OK"

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def click(self) -> None:
        self.clicks += 1
        if self._on_click is not None:
            self._on_click()


class DelayedLocator(FlowLocator):
    def __init__(
        self,
        *,
        missing_counts: int,
        present: Callable[[], bool],
        on_missing: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(present=present)
        self.missing_counts = missing_counts
        self.on_missing = on_missing

    async def count(self) -> int:
        if self.missing_counts > 0:
            self.missing_counts -= 1
            if self.on_missing is not None:
                self.on_missing()
            return 0
        return await super().count()


class DuplicateVisibleLocator(FlowLocator):
    async def count(self) -> int:
        return 2

    def nth(self, index: int) -> FlowLocator:
        if index not in {0, 1}:
            raise IndexError(index)
        return self


class HiddenAccountValue(FlowLocator):
    def __init__(
        self,
        value: str,
        *,
        index: int,
        reads: list[int],
        sensitive_error: str | None,
    ) -> None:
        super().__init__()
        self.value = value
        self.index = index
        self.reads = reads
        self.sensitive_error = sensitive_error

    async def input_value(self) -> str:
        self.reads.append(self.index)
        if self.sensitive_error is not None:
            raise RuntimeError(f"{self.sensitive_error}:{self.value}")
        return self.value


class HiddenAccountLocator(FlowLocator):
    def __init__(
        self,
        values: tuple[str, ...],
        *,
        present: Callable[[], bool],
        sensitive_error: str | None = None,
    ) -> None:
        super().__init__(present=present)
        self.values = values
        self.sensitive_error = sensitive_error
        self.reads: list[int] = []

    async def count(self) -> int:
        return len(self.values) if self._present() else 0

    def nth(self, index: int) -> HiddenAccountValue:
        if not 0 <= index < len(self.values):
            raise IndexError(index)
        return HiddenAccountValue(
            self.values[index],
            index=index,
            reads=self.reads,
            sensitive_error=self.sensitive_error,
        )


class FailingFillLocator(FlowLocator):
    def __init__(self, sensitive_message: str, *, present: Callable[[], bool]) -> None:
        super().__init__(present=present)
        self.sensitive_message = sensitive_message

    async def fill(self, value: str) -> None:
        raise RuntimeError(f"{self.sensitive_message}:{value}")


class FailingEvaluateLocator(FlowLocator):
    def __init__(self, sensitive_message: str, *, present: Callable[[], bool]) -> None:
        super().__init__(present=present)
        self.sensitive_message = sensitive_message

    async def evaluate(self, expression: str, argument: object = None) -> object:
        raise RuntimeError(f"{self.sensitive_message}:{expression}:{argument}")


class FailingProbeLocator(FlowLocator):
    def __init__(
        self,
        failure: str,
        sensitive_message: str,
        *,
        present: Callable[[], bool],
    ) -> None:
        super().__init__(present=present)
        self.failure = failure
        self.sensitive_message = sensitive_message

    async def count(self) -> int:
        if self.failure == "count":
            raise RuntimeError(self.sensitive_message)
        return await super().count()

    async def is_visible(self) -> bool:
        if self.failure == "is_visible":
            raise RuntimeError(self.sensitive_message)
        return await super().is_visible()


class TransientFailingProbeLocator(FlowLocator):
    def __init__(self, *, failures: int, present: Callable[[], bool]) -> None:
        super().__init__(present=present)
        self.failures = failures

    async def count(self) -> int:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("synthetic transient probe failure")
        return await super().count()


class ManualMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class DeadlineAdvancingLocator(FlowLocator):
    def __init__(self, clock: ManualMonotonic, *, present: Callable[[], bool]) -> None:
        super().__init__(present=present)
        self.clock = clock

    async def count(self) -> int:
        count = await super().count()
        self.clock.value = 15.0
        return count


class PendingClickLocator(FlowLocator):
    async def click(self) -> None:
        self.clicks += 1
        await asyncio.Event().wait()


class PendingProbeLocator(FlowLocator):
    def __init__(self, *, present: Callable[[], bool]) -> None:
        super().__init__(present=present)
        self.entered = asyncio.Event()

    async def count(self) -> int:
        self.entered.set()
        await asyncio.Event().wait()
        return 0


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
        dic_submit_via_role: bool = False,
        login_result: str = "login",
        identity_accounts: tuple[str, ...] = (
            "synthetic@example.invalid",
            "synthetic@example.invalid",
        ),
        identity_account_read_error: str | None = None,
    ) -> None:
        self._url = "about:blank"
        self.authenticated = authenticated
        self.captcha_enabled = captcha
        self.session_result = session_result
        self.email_result = email_result
        self.identity_result = identity_result
        self.response = response or FakeResponse()
        self.stale_response = stale_response
        self.dic_submit_via_role = dic_submit_via_role
        self.login_result = login_result
        self.handlers: list[Callable[[FakeResponse], None]] = []
        self.goto_urls: list[str] = []
        self.waits = 0
        self.identity_secret_value = ""
        self.dic_email = FlowLocator(
            present=lambda: self._url == f"{DIC_ORIGIN}/it/login",
        )
        self.dic_email_placeholder_matches = DuplicateVisibleLocator(
            present=lambda: self._url == f"{DIC_ORIGIN}/it/login",
        )
        self.dic_submit = FlowLocator(
            present=lambda: self._url == f"{DIC_ORIGIN}/it/login",
            on_click=lambda: self._set_url(f"{IDENTITY_ORIGIN}/Account/LoginEmail?flow=x"),
        )
        self.identity_email = FlowLocator(
            present=self._identity_email_route,
        )
        self.identity_email_submit = FlowLocator(
            present=self._identity_email_route,
            on_click=self._finish_email,
        )
        self.identity_password = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginPassword"),
        )
        self.identity_password._on_evaluate = lambda expression, argument: (
            self._atomic_credential_boundary(
                self.identity_password,
                "fill",
                expression,
                argument,
            )
        )
        self.identity_password_submit = FlowLocator(
            present=lambda: self._identity_path("/Account/LoginPassword"),
            on_click=self._finish_password,
        )
        self.identity_password_submit._on_evaluate = lambda expression, argument: (
            self._atomic_credential_boundary(
                self.identity_password_submit,
                "submit",
                expression,
                argument,
            )
        )
        self.identity_accounts = HiddenAccountLocator(
            identity_accounts,
            present=lambda: self._identity_path("/Account/LoginPassword"),
            sensitive_error=identity_account_read_error,
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

    def _identity_email_route(self) -> bool:
        return (
            self._identity_path("/Account/LoginEmail")
            or self._url
            in {
                IDENTITY_ORIGIN,
                f"{IDENTITY_ORIGIN}/",
            }
            or self._url.startswith(f"{IDENTITY_ORIGIN}/?")
        )

    def _set_url(self, url: str) -> None:
        self._url = url

    def _atomic_credential_boundary(
        self,
        locator: FlowLocator,
        operation: str,
        expression: str,
        argument: object,
    ) -> object:
        del expression
        try:
            exact_route = PlaywrightAuthenticator._is_exact_route(
                self.url,
                IDENTITY_ORIGIN,
                "/Account/LoginPassword",
            )
        except DicAuthenticationError:
            exact_route = False
        if not exact_route or not isinstance(argument, dict):
            return "CONTROL_INVALID"
        if self.captcha_enabled:
            return "CAPTCHA_PRESENT"
        username = argument.get("username")
        if not isinstance(username, str) or not username.strip():
            return "CONTROL_INVALID"
        expected = username.strip().casefold()
        if not 1 <= len(self.identity_accounts.values) <= 4 or any(
            value.strip().casefold() != expected for value in self.identity_accounts.values
        ):
            return "CONTROL_INVALID"
        if operation == "fill":
            secret = argument.get("secret")
            if (
                locator is not self.identity_password
                or not locator._present()
                or not isinstance(secret, str)
                or not secret
            ):
                return "CONTROL_INVALID"
            self.identity_secret_value = secret
            locator.filled.append(secret)
            return "CONTROL_OK"
        if (
            operation != "submit"
            or locator is not self.identity_password_submit
            or not locator._present()
            or not self.identity_secret_value
        ):
            return "CONTROL_INVALID"
        locator.clicks += 1
        if locator._on_click is not None:
            locator._on_click()
        return "CONTROL_OK"

    def _finish_password(self) -> None:
        if self.identity_result == "authenticated":
            self.authenticated = True
            self._url = f"{DIC_ORIGIN}/it/app/employees"
        elif self.identity_result == "callback":
            self.authenticated = True
            self._url = f"{DIC_ORIGIN}/it/callback?code=opaque-synthetic-value"
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
        if self.dic_submit_via_role and role == "button" and name == "Accedi" and exact is True:
            return self.dic_submit
        return self.missing

    def get_by_label(self, text: str, *, exact=None):
        del text, exact
        return self.missing

    def get_by_placeholder(self, text: str):
        if text == "Inserisci la tua e-mail":
            return self.dic_email_placeholder_matches
        return self.missing

    def get_by_test_id(self, test_id: str):
        if test_id == "login-submit" and not self.dic_submit_via_role:
            return self.dic_submit
        if test_id == "captcha":
            return self.captcha
        return self.missing

    def locator(self, selector: str):
        return {
            "[data-testid='login-email'] input": self.dic_email,
            "#EmailAddress_Email": self.identity_email,
            "#submitEmailBtn": self.identity_email_submit,
            "#Login_Email": self.identity_accounts,
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
                elif self.session_result == "root":
                    self._url = f"{IDENTITY_ORIGIN}/?flow=session"
                elif self.session_result == "authorize":
                    self._url = f"{IDENTITY_ORIGIN}/connect/authorize?flow=session"
                elif self.session_result == "authorize-callback":
                    self._url = f"{IDENTITY_ORIGIN}/connect/authorize/callback?flow=session"
                elif self.session_result == "dic-callback":
                    self._url = f"{DIC_ORIGIN}/it/callback?flow=session"
                else:
                    self._url = f"{IDENTITY_ORIGIN}/Account/LoginEmail?flow=session"
        elif url == f"{DIC_ORIGIN}/it/login":
            if self.login_result == "app":
                self.authenticated = True
                self._url = f"{DIC_ORIGIN}/it/app/employees"
            else:
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
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], object] | None = None,
) -> PlaywrightAuthenticator:
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    return PlaywrightAuthenticator(  # type: ignore[arg-type]
        page,
        DIC_ORIGIN,
        expected_tenant_id=tenant_id,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_restored_session_is_probed_from_about_blank_and_attested() -> None:
    page = AuthFlowPage(authenticated=True)

    result = await authenticator(page).status()

    assert result.state is SessionState.AUTHENTICATED
    assert page.goto_urls == ["about:blank", f"{DIC_ORIGIN}/it/app/employees/list"]
    assert page.handlers == []


@pytest.mark.asyncio
async def test_restored_session_waits_for_hydrated_authenticated_marker() -> None:
    page = AuthFlowPage(authenticated=True)
    page.marker = DelayedLocator(
        missing_counts=2,
        present=lambda: page.url == f"{DIC_ORIGIN}/it/app/employees/list",
    )

    result = await authenticator(
        page,
        monotonic=lambda: 0.0,
        sleeper=_no_sleep,
    ).status()

    assert result.state is SessionState.AUTHENTICATED
    assert page.marker.missing_counts == 0
    assert page.handlers == []


@pytest.mark.asyncio
async def test_restored_session_marker_hydration_timeout_fails_closed() -> None:
    page = AuthFlowPage(authenticated=True)
    page.marker = FlowLocator(present=lambda: False)
    clock = ManualMonotonic()

    async def expire_probe(_delay: float) -> None:
        clock.value = 15.0

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(
            page,
            monotonic=clock,
            sleeper=expire_probe,
        ).status()

    assert caught.value.stage is DicAuthStage.SESSION_PROBE
    assert page.handlers == []


@pytest.mark.asyncio
async def test_session_status_is_unknown_after_exact_identity_redirect() -> None:
    page = AuthFlowPage(authenticated=False)

    assert (await authenticator(page).status()).state is SessionState.UNKNOWN
    assert page.handlers == []


@pytest.mark.parametrize(
    "session_result",
    ("root", "authorize", "authorize-callback", "dic-callback"),
)
@pytest.mark.asyncio
async def test_session_status_is_unknown_on_exact_federated_entry_routes(
    session_result: str,
) -> None:
    page = AuthFlowPage(session_result=session_result)

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
async def test_current_teamsystem_root_email_form_completes_login() -> None:
    page = AuthFlowPage()
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(f"{IDENTITY_ORIGIN}/?flow=opaque-synthetic-value"),
    )

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_email_submit.clicks == 1
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_exact_authorize_routes_are_bounded_pending_before_root_email_form() -> None:
    page = AuthFlowPage()
    transitions = [
        f"{IDENTITY_ORIGIN}/connect/authorize/callback?flow=opaque-synthetic-value",
        f"{IDENTITY_ORIGIN}/?flow=opaque-synthetic-value",
    ]
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/connect/authorize?flow=opaque-synthetic-value"
        ),
    )

    async def advance_redirect(_delay: float) -> None:
        if transitions:
            page._set_url(transitions.pop(0))

    result = await authenticator(page, sleeper=advance_redirect).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert transitions == []
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_existing_idp_sso_is_tenant_attested_without_credential_actions() -> None:
    page = AuthFlowPage()
    transitions = [
        f"{IDENTITY_ORIGIN}/connect/authorize/callback?flow=opaque-synthetic-value",
        f"{DIC_ORIGIN}/it/callback?code=opaque-synthetic-value",
        f"{DIC_ORIGIN}/it/app/dashboard",
    ]
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/connect/authorize?flow=opaque-synthetic-value"
        ),
    )

    async def complete_sso(_delay: float) -> None:
        if transitions:
            next_url = transitions.pop(0)
            page._set_url(next_url)
            if next_url == f"{DIC_ORIGIN}/it/app/dashboard":
                page.authenticated = True

    result = await authenticator(page, sleeper=complete_sso).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert transitions == []
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_existing_idp_sso_rejects_wrong_tenant_without_credential_actions() -> None:
    class OtherTenantResponse(FakeResponse):
        async def body(self) -> bytes:
            return b'{"data":{"company":{"id":987654321}}}'

    page = AuthFlowPage(response=OtherTenantResponse())
    transitions = [
        f"{DIC_ORIGIN}/it/callback?code=opaque-synthetic-value",
        f"{DIC_ORIGIN}/it/app/dashboard",
    ]
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/connect/authorize?flow=opaque-synthetic-value"
        ),
    )

    async def complete_sso(_delay: float) -> None:
        if transitions:
            next_url = transitions.pop(0)
            page._set_url(next_url)
            if next_url == f"{DIC_ORIGIN}/it/app/dashboard":
                page.authenticated = True

    with pytest.raises(DicAuthorizationError, match="does not match"):
        await authenticator(page, sleeper=complete_sso).authenticate(credentials())

    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_email_route_reclassifies_if_captcha_probe_advances_to_password() -> None:
    page = AuthFlowPage()

    def enter_email_route() -> None:
        page._set_url(f"{IDENTITY_ORIGIN}/Account/LoginEmail?flow=synthetic")
        page.captcha = DelayedLocator(
            missing_counts=1,
            present=lambda: False,
            on_missing=lambda: page._set_url(
                f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
            ),
        )

    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=enter_email_route,
    )

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_email_control_hydration_may_advance_to_exact_password_route() -> None:
    page = AuthFlowPage()
    page.identity_email = DelayedLocator(
        missing_counts=1,
        present=lambda: page._identity_email_route(),
        on_missing=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
        ),
    )

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_email_submit_hydration_may_advance_to_exact_password_route() -> None:
    page = AuthFlowPage()
    page.identity_email_submit = DelayedLocator(
        missing_counts=1,
        present=lambda: page._identity_email_route(),
        on_missing=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
        ),
    )

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_transient_entry_probe_error_retries_without_credential_action() -> None:
    page = AuthFlowPage()

    def enter_password_route() -> None:
        page._set_url(f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value")
        page.captcha = TransientFailingProbeLocator(
            failures=1,
            present=lambda: False,
        )

    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=enter_password_route,
    )

    result = await authenticator(
        page,
        monotonic=lambda: 0.0,
        sleeper=_no_sleep,
    ).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.captcha.failures == 0
    assert page.identity_email.filled == []
    assert page.identity_password.filled == ["synthetic-password"]


@pytest.mark.asyncio
async def test_permanent_entry_probe_error_times_out_before_credentials() -> None:
    page = AuthFlowPage()
    clock = ManualMonotonic()

    def enter_password_route() -> None:
        page._set_url(f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value")
        page.captcha = FailingProbeLocator(
            "count",
            "synthetic permanent probe failure",
            present=lambda: False,
        )

    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=enter_password_route,
    )

    async def expire_flow(_delay: float) -> None:
        clock.value = 15.0

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(
            page,
            monotonic=clock,
            sleeper=expire_flow,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_EMAIL
    assert page.identity_email.filled == []
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_dic_login_hint_may_enter_exact_teamsystem_password_route_directly() -> None:
    page = AuthFlowPage()
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
        ),
    )

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.dic_email.filled == ["synthetic@example.invalid"]
    assert page.dic_submit.clicks == 1
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_accounts.reads == [0, 1]
    assert page.identity_password.filled == ["synthetic-password"]
    assert page.identity_password_submit.clicks == 1


@pytest.mark.parametrize(
    "identity_accounts",
    [
        (),
        ("", ""),
        ("different@example.invalid",),
        ("synthetic@example.invalid", "different@example.invalid"),
    ],
)
@pytest.mark.asyncio
async def test_direct_teamsystem_password_route_requires_exact_account_binding(
    identity_accounts: tuple[str, ...],
) -> None:
    page = AuthFlowPage(identity_accounts=identity_accounts)
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
        ),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_email_then_password_route_requires_exact_account_binding() -> None:
    page = AuthFlowPage(identity_accounts=("different@example.invalid",))

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_email_submit.clicks == 1
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_teamsystem_account_binding_read_failure_is_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive = "private-identity-account-marker"
    page = AuthFlowPage(identity_account_read_error=sensitive)
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=opaque-synthetic-value"
        ),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert sensitive not in caplog.text
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_password_route_change_during_visibility_never_sets_secret() -> None:
    page = AuthFlowPage()
    page.identity_password._on_visible = lambda: page._set_url(
        "https://identity.teamsystem.com.evil.invalid/Account/LoginPassword"
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
    assert page.identity_password.filled == []
    assert page.identity_secret_value == ""
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_late_captcha_during_password_visibility_never_sets_secret() -> None:
    page = AuthFlowPage()
    page.identity_password._on_visible = lambda: setattr(page, "captcha_enabled", True)

    with pytest.raises(DicCaptchaRequiredError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL  # type: ignore[attr-defined]
    assert page.identity_password.filled == []
    assert page.identity_secret_value == ""
    assert page.identity_password_submit.clicks == 0


@pytest.mark.parametrize("drift", ["account", "route", "captcha"])
@pytest.mark.asyncio
async def test_submit_boundary_rejects_late_drift_without_dispatch(drift: str) -> None:
    page = AuthFlowPage()

    def mutate_submit_precondition() -> None:
        if drift == "account":
            page.identity_accounts.values = ("different@example.invalid",)
        elif drift == "route":
            page._set_url("https://identity.teamsystem.com.evil.invalid/Account/LoginPassword")
        else:
            page.captcha_enabled = True

    page.identity_password_submit._on_visible = mutate_submit_precondition

    if drift == "captcha":
        with pytest.raises(DicCaptchaRequiredError) as caught:
            await authenticator(page).authenticate(credentials())
    else:
        with pytest.raises(DicAuthUiChangedError) as caught:
            await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT  # type: ignore[attr-defined]
    assert page.identity_password.filled == ["synthetic-password"]
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_atomic_secret_evaluate_failure_is_redacted_before_submit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = AuthFlowPage()
    sensitive = "private-atomic-fill-marker"
    page.identity_password = FailingEvaluateLocator(
        sensitive,
        present=lambda: page._identity_path("/Account/LoginPassword"),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert "synthetic-password" not in str(caught.value)
    assert sensitive not in caplog.text
    assert "synthetic-password" not in caplog.text
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_atomic_submit_evaluate_failure_is_redacted_and_outcome_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = AuthFlowPage()
    sensitive = "private-atomic-submit-marker"
    page.identity_password_submit = FailingEvaluateLocator(
        sensitive,
        present=lambda: page._identity_path("/Account/LoginPassword"),
    )

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert "synthetic-password" not in str(caught.value)
    assert sensitive not in caplog.text
    assert "synthetic-password" not in caplog.text
    assert page.identity_password_submit.clicks == 0


@pytest.mark.parametrize(
    "password_url",
    [
        "https://identity.teamsystem.com.evil.invalid/Account/LoginPassword",
        "https://identity.teamsystem.com:443/Account/LoginPassword",
        "https://user@identity.teamsystem.com/Account/LoginPassword",
        f"{IDENTITY_ORIGIN}/Account/LoginPassword#fragment",
        f"{IDENTITY_ORIGIN}/Account/LoginPassword/",
        f"{IDENTITY_ORIGIN}/Account/LoginPassword/extra",
    ],
)
@pytest.mark.asyncio
async def test_dic_login_hint_rejects_nonexact_teamsystem_password_routes(
    password_url: str,
) -> None:
    page = AuthFlowPage()
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(password_url),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_EMAIL
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.parametrize(
    "entry_url",
    [
        IDENTITY_ORIGIN,
        f"{IDENTITY_ORIGIN}//",
        f"{IDENTITY_ORIGIN}/extra",
        f"{IDENTITY_ORIGIN}/#fragment",
        f"{IDENTITY_ORIGIN}:443/",
        "https://user@identity.teamsystem.com/",
        "https://identity.teamsystem.com.evil.invalid/",
        f"{IDENTITY_ORIGIN}/connect/authorize/",
        f"{IDENTITY_ORIGIN}/connect/authorize/extra",
        f"{IDENTITY_ORIGIN}/connect/authorize#fragment",
        f"{IDENTITY_ORIGIN}/connect/authorize/callback/",
        f"{IDENTITY_ORIGIN}/connect/authorize/callback/extra",
        f"{IDENTITY_ORIGIN}/connect/authorize/callback#fragment",
    ],
)
@pytest.mark.asyncio
async def test_federated_entry_rejects_nonexact_route_variants(entry_url: str) -> None:
    page = AuthFlowPage()
    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=lambda: page._set_url(entry_url),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_EMAIL
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_direct_teamsystem_password_route_still_enforces_captcha_guard() -> None:
    page = AuthFlowPage()

    def enter_password_with_captcha() -> None:
        page._set_url(f"{IDENTITY_ORIGIN}/Account/LoginPassword?flow=synthetic")
        page.captcha_enabled = True

    page.dic_submit = FlowLocator(
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
        on_click=enter_password_with_captcha,
    )

    with pytest.raises(DicCaptchaRequiredError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_EMAIL  # type: ignore[attr-defined]
    assert page.identity_email.filled == []
    assert page.identity_email_submit.clicks == 0
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.parametrize(
    "callback_url",
    [
        f"{DIC_ORIGIN}/it/callback",
        f"{DIC_ORIGIN}/it/callback?code=opaque-synthetic-value",
    ],
)
@pytest.mark.asyncio
async def test_exact_dic_callback_is_pending_until_app_route_then_attests_tenant(
    callback_url: str,
) -> None:
    page = AuthFlowPage()

    def enter_callback() -> None:
        page.authenticated = True
        page._set_url(callback_url)

    page.identity_password_submit = FlowLocator(
        present=lambda: page._identity_path("/Account/LoginPassword"),
        on_click=enter_callback,
    )

    async def complete_callback(_delay: float) -> None:
        if page.url.startswith(f"{DIC_ORIGIN}/it/callback"):
            page._set_url(f"{DIC_ORIGIN}/it/app/dashboard")

    result = await authenticator(page, sleeper=complete_callback).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_password_submit.clicks == 1
    assert page.goto_urls == [
        f"{DIC_ORIGIN}/it/login",
        "about:blank",
        f"{DIC_ORIGIN}/it/app/employees/list",
    ]


@pytest.mark.asyncio
async def test_exact_teamsystem_authorize_callback_is_pending_post_password() -> None:
    page = AuthFlowPage()

    def enter_callback() -> None:
        page._set_url(f"{IDENTITY_ORIGIN}/connect/authorize/callback?flow=opaque-synthetic-value")
        page.captcha = FailingProbeLocator(
            "count",
            "callback document has no stable controls",
            present=lambda: False,
        )

    page.identity_password_submit = FlowLocator(
        present=lambda: page._identity_path("/Account/LoginPassword"),
        on_click=enter_callback,
    )

    async def complete_callback(_delay: float) -> None:
        if page.url.startswith(f"{IDENTITY_ORIGIN}/connect/authorize/callback"):
            page._set_url(f"{DIC_ORIGIN}/it/callback?code=opaque-synthetic-value")
        elif page.url.startswith(f"{DIC_ORIGIN}/it/callback"):
            page.authenticated = True
            page._set_url(f"{DIC_ORIGIN}/it/app/dashboard")

    result = await authenticator(page, sleeper=complete_callback).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_dic_email_fill_uses_native_input_despite_ambiguous_placeholder() -> None:
    page = AuthFlowPage()

    await authenticator(page).authenticate(credentials())

    assert await page.dic_email_placeholder_matches.count() == 2
    assert page.dic_email_placeholder_matches.filled == []
    assert page.dic_email.filled == ["synthetic@example.invalid"]


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

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT


@pytest.mark.asyncio
async def test_sso_completion_is_accepted_only_after_tenant_attestation() -> None:
    page = AuthFlowPage(email_result="passwordless")

    result = await authenticator(page).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.identity_email.filled == ["synthetic@example.invalid"]
    assert page.identity_email_submit.clicks == 1
    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_sso_completion_rejects_a_different_tenant_without_password_action() -> None:
    class OtherTenantResponse(FakeResponse):
        async def body(self) -> bytes:
            return b'{"data":{"company":{"id":987654321}}}'

    page = AuthFlowPage(
        email_result="passwordless",
        response=OtherTenantResponse(),
    )

    with pytest.raises(DicAuthorizationError, match="does not match"):
        await authenticator(page).authenticate(credentials())

    assert page.identity_password.filled == []
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_mfa_fails_closed_even_when_totp_is_configured() -> None:
    page = AuthFlowPage(identity_result="mfa")

    with pytest.raises(DicMfaRequiredError, match="interactive MFA"):
        await authenticator(page).authenticate(credentials(totp="123456"))

    assert page.mfa.filled == []
    assert page.identity_password_submit.clicks == 1


async def _no_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_hydrated_control_is_waited_for_within_shared_deadline() -> None:
    page = AuthFlowPage()
    page.dic_email = DelayedLocator(
        missing_counts=3,
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
    )

    result = await authenticator(
        page,
        monotonic=lambda: 0.0,
        sleeper=_no_sleep,
    ).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED
    assert page.dic_email.missing_counts == 0
    assert page.dic_email.filled == ["synthetic@example.invalid"]


@pytest.mark.asyncio
async def test_duplicate_visible_control_fails_closed_with_stage() -> None:
    page = AuthFlowPage()
    page.dic_email = DuplicateVisibleLocator(present=lambda: page.url == f"{DIC_ORIGIN}/it/login")

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL
    assert page.dic_email.filled == []


@pytest.mark.asyncio
async def test_control_timeout_fails_with_exact_stage_without_real_wait() -> None:
    page = AuthFlowPage()
    page.dic_email = FlowLocator(present=lambda: False)
    ticks = iter((0.0, 0.0, 61.0))

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(
            page,
            monotonic=lambda: next(ticks),
            sleeper=_no_sleep,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL


@pytest.mark.asyncio
async def test_route_lookalike_during_hydration_fails_with_stage() -> None:
    page = AuthFlowPage()
    page.dic_email = DelayedLocator(
        missing_counts=1,
        present=lambda: False,
        on_missing=lambda: page._set_url(
            "https://secure.dipendentincloud.it.evil.invalid/it/login"
        ),
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(
            page,
            monotonic=lambda: 0.0,
            sleeper=_no_sleep,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL


@pytest.mark.asyncio
async def test_late_captcha_interrupts_hydration_before_secret_fill() -> None:
    page = AuthFlowPage()
    page.dic_email = DelayedLocator(
        missing_counts=1,
        present=lambda: True,
        on_missing=lambda: setattr(page, "captcha_enabled", True),
    )

    with pytest.raises(DicCaptchaRequiredError) as caught:
        await authenticator(
            page,
            monotonic=lambda: 0.0,
            sleeper=_no_sleep,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL  # type: ignore[attr-defined]
    assert page.dic_email.filled == []


@pytest.mark.parametrize("failure", ["count", "is_visible"])
@pytest.mark.asyncio
async def test_captcha_probe_errors_fail_closed_without_provider_details(failure: str) -> None:
    page = AuthFlowPage()
    sensitive = "private-captcha-provider-marker"
    page.captcha = FailingProbeLocator(
        failure,
        sensitive,
        present=lambda: True,
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert page.dic_email.filled == []


@pytest.mark.parametrize("failure", ["count", "is_visible"])
@pytest.mark.asyncio
async def test_mfa_probe_errors_become_nonrepeatable_unknown_outcome(failure: str) -> None:
    page = AuthFlowPage(identity_result="mfa")
    sensitive = "private-mfa-provider-marker"
    page.mfa = FailingProbeLocator(
        failure,
        sensitive,
        present=lambda: True,
    )

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_control_visible_only_after_shared_deadline_is_never_filled() -> None:
    page = AuthFlowPage()
    clock = ManualMonotonic()
    page.dic_email = DeadlineAdvancingLocator(
        clock,
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(
            page,
            monotonic=clock,
            sleeper=_no_sleep,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL
    assert page.dic_email.filled == []


@pytest.mark.asyncio
async def test_expired_shared_deadline_before_password_submit_never_dispatches() -> None:
    page = AuthFlowPage()
    clock = ManualMonotonic()

    class ExpiringBeforePasswordSubmitAuthenticator(PlaywrightAuthenticator):
        async def _wait_for_control(self, key: str, *args, **kwargs):
            control = await super()._wait_for_control(key, *args, **kwargs)
            if key == "auth.teamsystem_password_submit":
                clock.value = 15.0
            return control

    auth = ExpiringBeforePasswordSubmitAuthenticator(  # type: ignore[arg-type]
        page,
        DIC_ORIGIN,
        expected_tenant_id=EXPECTED_TENANT,
        monotonic=clock,
        sleeper=_no_sleep,
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await auth.authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_password_submit_dispatched_then_provider_raises_is_outcome_unknown() -> None:
    page = AuthFlowPage()
    sensitive = "private-post-dispatch-provider-marker"

    def dispatched_then_raise() -> None:
        raise RuntimeError(sensitive)

    page.identity_password_submit = FlowLocator(
        present=lambda: page._identity_path("/Account/LoginPassword"),
        on_click=dispatched_then_raise,
    )

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert page.identity_password_submit.clicks == 1


@pytest.mark.parametrize(
    "callback_url",
    [
        "https://secure.dipendentincloud.it.evil.invalid/it/callback?code=synthetic",
        "https://secure.dipendentincloud.it:443/it/callback?code=synthetic",
        "https://user@secure.dipendentincloud.it/it/callback?code=synthetic",
        "https://secure.dipendentincloud.it/it/callback?code=synthetic#fragment",
        "https://secure.dipendentincloud.it/it/callback/",
        "https://secure.dipendentincloud.it/it/callback/extra?code=synthetic",
    ],
)
@pytest.mark.asyncio
async def test_post_submit_callback_variants_fail_closed(callback_url: str) -> None:
    page = AuthFlowPage()
    page.identity_password_submit = FlowLocator(
        present=lambda: page._identity_path("/Account/LoginPassword"),
        on_click=lambda: page._set_url(callback_url),
    )

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert callback_url not in str(caught.value)
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_session_probe_route_change_during_marker_hydration_fails_closed() -> None:
    page = AuthFlowPage(authenticated=True)
    page.marker = DelayedLocator(
        missing_counts=1,
        present=lambda: True,
        on_missing=lambda: page._set_url(f"{DIC_ORIGIN}/it/app/unexpected"),
    )

    with pytest.raises(DicAuthenticationError, match="unexpected route"):
        await authenticator(
            page,
            monotonic=lambda: 0.0,
            sleeper=_no_sleep,
        ).status()

    assert page.handlers == []


@pytest.mark.asyncio
async def test_post_submit_callback_timeout_does_not_expose_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = AuthFlowPage()
    clock = ManualMonotonic()
    query_marker = "private-oauth-query-marker"
    page.identity_password_submit = FlowLocator(
        present=lambda: page._identity_path("/Account/LoginPassword"),
        on_click=lambda: page._set_url(
            f"{DIC_ORIGIN}/it/callback?code={query_marker}&state=opaque"
        ),
    )

    async def expire_flow(_delay: float) -> None:
        clock.value = 15.0

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(
            page,
            monotonic=clock,
            sleeper=expire_flow,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert query_marker not in str(caught.value)
    assert query_marker not in caplog.text
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_pending_password_submit_times_out_inside_auth_boundary() -> None:
    page = AuthFlowPage()
    pending = PendingClickLocator(present=lambda: True)
    auth = authenticator(page)
    auth._flow_deadline = time.monotonic() + 5

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await asyncio.wait_for(
            auth._click_control(
                pending,
                DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT,
                outcome_unknown_on_failure=True,
            ),
            timeout=0.01,
        )

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert pending.clicks == 1


@pytest.mark.asyncio
async def test_outer_cancellation_during_post_submit_probe_is_outcome_unknown() -> None:
    page = AuthFlowPage(identity_result="mfa")
    page.mfa = PendingProbeLocator(present=lambda: True)
    operation = asyncio.create_task(
        asyncio.wait_for(
            authenticator(page).authenticate(credentials()),
            timeout=0.05,
        )
    )

    await asyncio.wait_for(page.mfa.entered.wait(), timeout=1)
    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await operation

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_post_submit_completion_timeout_is_outcome_unknown() -> None:
    page = AuthFlowPage(identity_result="stuck")
    clock = ManualMonotonic()

    async def expire_flow(_delay: float) -> None:
        clock.value = 15.0

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(
            page,
            monotonic=clock,
            sleeper=expire_flow,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_post_submit_tenant_probe_failure_is_outcome_unknown() -> None:
    class OtherTenantResponse(FakeResponse):
        async def body(self) -> bytes:
            return b'{"data":{"company":{"id":987654321}}}'

    page = AuthFlowPage(response=OtherTenantResponse())

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert page.identity_password_submit.clicks == 1


@pytest.mark.asyncio
async def test_post_submit_status_construction_failure_is_outcome_unknown() -> None:
    page = AuthFlowPage()
    sensitive = "private-status-clock-marker"

    def failing_clock() -> datetime:
        raise RuntimeError(sensitive)

    auth = PlaywrightAuthenticator(  # type: ignore[arg-type]
        page,
        DIC_ORIGIN,
        expected_tenant_id=EXPECTED_TENANT,
        clock=failing_clock,
    )

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await auth.authenticate(credentials())

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)


@pytest.mark.asyncio
async def test_restored_app_probe_cannot_return_success_after_shared_deadline() -> None:
    page = AuthFlowPage(login_result="app")
    clock = ManualMonotonic()

    class LateProbeAuthenticator(PlaywrightAuthenticator):
        async def _probe_authenticated_session(self) -> bool:
            clock.value = 15.0
            return True

    auth = LateProbeAuthenticator(  # type: ignore[arg-type]
        page,
        DIC_ORIGIN,
        expected_tenant_id=EXPECTED_TENANT,
        monotonic=clock,
        sleeper=_no_sleep,
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await auth.authenticate(credentials())

    assert caught.value.stage is DicAuthStage.SESSION_PROBE
    assert page.identity_password_submit.clicks == 0


@pytest.mark.asyncio
async def test_password_expiry_during_credential_hydration_keeps_stage() -> None:
    page = AuthFlowPage()
    page.identity_password = DelayedLocator(
        missing_counts=1,
        present=lambda: False,
        on_missing=lambda: page._set_url(
            f"{IDENTITY_ORIGIN}/Account/LoginPasswordExpired?flow=synthetic"
        ),
    )

    with pytest.raises(DicPasswordExpiredError) as caught:
        await authenticator(
            page,
            monotonic=lambda: 0.0,
            sleeper=_no_sleep,
        ).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_verified_dic_submit_role_fallback_completes_login() -> None:
    result = await authenticator(AuthFlowPage(dic_submit_via_role=True)).authenticate(credentials())

    assert result.state is SessionState.AUTHENTICATED


@pytest.mark.asyncio
async def test_provider_fill_exception_is_replaced_without_secret_context() -> None:
    page = AuthFlowPage()
    sensitive = "provider-private-marker"
    page.dic_email = FailingFillLocator(
        sensitive,
        present=lambda: page.url == f"{DIC_ORIGIN}/it/login",
    )

    with pytest.raises(DicAuthUiChangedError) as caught:
        await authenticator(page).authenticate(credentials())

    assert caught.value.stage is DicAuthStage.DIC_EMAIL
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert "synthetic@example.invalid" not in str(caught.value)
