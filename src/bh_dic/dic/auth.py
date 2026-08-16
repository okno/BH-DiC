"""Deterministic login flow and encrypted browser-session lifecycle."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from bh_dic.dic.errors import (
    DicAuthenticationError,
    DicAuthorizationError,
    DicCaptchaRequiredError,
    DicConfigurationError,
    DicMfaRequiredError,
    DicPasswordExpiredError,
    DicSessionExpiredError,
    DicUiChangedError,
)
from bh_dic.dic.models import (
    DicCredentials,
    SessionState,
    SessionStatus,
    StoredBrowserSession,
)
from bh_dic.dic.pages import EmployeesListPage, LocatorLike, LoginPage, PageLike
from bh_dic.dic.session_vault import FernetSessionVault
from bh_dic.dic.tenant_attestation import ResponseEventSource, TenantResponseCapture

_DIC_ORIGIN = "https://secure.dipendentincloud.it"
_TEAMSYSTEM_ORIGIN = "https://identity.teamsystem.com"
_TEAMSYSTEM_EMAIL_PATH = "/Account/LoginEmail"
_TEAMSYSTEM_SECRET_ENTRY_PATH = "/Account/Login" + "Password"
_TEAMSYSTEM_SECRET_RENEWAL_PATH = _TEAMSYSTEM_SECRET_ENTRY_PATH + "Expired"
_DIC_AUTH_CALLBACK_PATH = "/it/callback"
_CANONICAL_TENANT_ID = re.compile(r"[1-9][0-9]{0,18}")
_AUTH_POLL_SECONDS = 0.1


class DicAuthStage(StrEnum):
    """Closed, non-sensitive authentication stages safe for operator output."""

    UNCLASSIFIED = "UNCLASSIFIED"
    DIC_EMAIL = "DIC_EMAIL"
    DIC_SUBMIT = "DIC_SUBMIT"
    TEAMSYSTEM_EMAIL = "TEAMSYSTEM_EMAIL"
    TEAMSYSTEM_EMAIL_SUBMIT = "TEAMSYSTEM_EMAIL_SUBMIT"
    TEAMSYSTEM_CREDENTIAL = "TEAMSYSTEM_CREDENTIAL"
    TEAMSYSTEM_CREDENTIAL_SUBMIT = "TEAMSYSTEM_CREDENTIAL_SUBMIT"
    CREDENTIAL_SUBMIT = "CREDENTIAL_SUBMIT"
    SESSION_PROBE = "SESSION_PROBE"


class DicAuthUiChangedError(DicUiChangedError, DicAuthenticationError):
    """An expected authentication control or route was unavailable."""

    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC authentication UI is unavailable")


class DicAuthCaptchaRequiredError(DicCaptchaRequiredError):
    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC login requires human CAPTCHA verification")


class DicAuthPasswordExpiredError(DicPasswordExpiredError):
    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC password renewal is required")


class DicAuthMfaRequiredError(DicMfaRequiredError):
    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC login requires interactive MFA verification")


class DicAuthCompletionError(DicAuthenticationError):
    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC login did not reach an authenticated route")


class DicAuthOutcomeUnknownError(DicAuthenticationError):
    """A credential submission may have reached the identity provider."""

    def __init__(self, stage: DicAuthStage) -> None:
        self.stage = stage
        super().__init__("DIC authentication outcome is unknown")


class _AuthProbePending(Exception):
    """A provider locator was transiently unavailable during bounded polling."""


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
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
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
        self._monotonic = monotonic
        self._sleep = sleeper
        self._flow_deadline: float | None = None
        self._authenticated_at: datetime | None = None

    @property
    def login_timeout_seconds(self) -> float:
        return self.login_page.timeout_ms / 1_000

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
    def _raise_if_password_expired(
        cls,
        url: str,
        stage: DicAuthStage = DicAuthStage.UNCLASSIFIED,
    ) -> None:
        if cls._is_exact_route(url, _TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_SECRET_RENEWAL_PATH):
            raise DicAuthPasswordExpiredError(stage)

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

    def _session_probe_is_on_target_route(self) -> bool:
        stage = DicAuthStage.SESSION_PROBE
        self._raise_if_password_expired(self.page.url, stage)
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
        return True

    async def _wait_for_authenticated_marker(self, *, deadline: float) -> bool:
        stage = DicAuthStage.SESSION_PROBE
        while True:
            self._raise_if_deadline_reached(deadline, stage)
            if not self._session_probe_is_on_target_route():
                return False
            try:
                marker = await self._candidate_visible(
                    "auth.authenticated",
                    stage,
                    deadline=deadline,
                    allow_multiple=True,
                )
            except _AuthProbePending:
                marker = None
            if marker is not None:
                self._raise_if_deadline_reached(deadline, stage)
                if not self._session_probe_is_on_target_route():
                    return False
                return True
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DicAuthUiChangedError(stage)
            await self._sleep(min(_AUTH_POLL_SECONDS, remaining))

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
            probe_deadline = self._control_deadline()
            remaining = probe_deadline - self._monotonic()
            if remaining <= 0:
                raise DicAuthUiChangedError(DicAuthStage.SESSION_PROBE)
            try:
                await asyncio.wait_for(
                    self.employees_page.navigate(),
                    timeout=remaining,
                )
            except Exception:
                raise DicAuthenticationError("DIC session probe navigation failed") from None

            if not await self._wait_for_authenticated_marker(deadline=probe_deadline):
                return False
            remaining = probe_deadline - self._monotonic()
            if remaining <= 0:
                raise DicAuthUiChangedError(DicAuthStage.SESSION_PROBE)
            try:
                await asyncio.wait_for(
                    capture.attest(
                        self.expected_tenant_id,
                        timeout_ms=remaining * 1_000,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                raise DicAuthenticationError("DIC session probe timed out") from None
        return True

    async def _candidate_visible(
        self,
        key: str,
        stage: DicAuthStage,
        *,
        deadline: float,
        allow_multiple: bool = False,
        strict_probe_errors: bool = False,
        completion_deadline: bool = False,
    ) -> LocatorLike | None:
        for candidate in self.login_page.selectors.candidates(key):
            self._raise_if_deadline_reached(
                deadline,
                stage,
                completion=completion_deadline,
            )
            probe_failed = False
            try:
                locator = self.login_page._candidate_locator(self.page, candidate)
            except Exception:
                probe_failed = True
            if probe_failed:
                if strict_probe_errors:
                    raise DicAuthUiChangedError(stage)
                raise _AuthProbePending
            self._raise_if_deadline_reached(
                deadline,
                stage,
                completion=completion_deadline,
            )
            remaining = deadline - self._monotonic()
            try:
                count = await asyncio.wait_for(locator.count(), timeout=remaining)
            except Exception:
                probe_failed = True
                count = 0
            if probe_failed:
                if strict_probe_errors:
                    raise DicAuthUiChangedError(stage)
                raise _AuthProbePending
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 128:
                raise DicAuthUiChangedError(stage)
            visible: LocatorLike | None = None
            for index in range(count):
                self._raise_if_deadline_reached(
                    deadline,
                    stage,
                    completion=completion_deadline,
                )
                try:
                    item = locator.nth(index)
                except Exception:
                    probe_failed = True
                    break
                self._raise_if_deadline_reached(
                    deadline,
                    stage,
                    completion=completion_deadline,
                )
                remaining = deadline - self._monotonic()
                try:
                    is_visible = await asyncio.wait_for(
                        item.is_visible(),
                        timeout=remaining,
                    )
                except Exception:
                    probe_failed = True
                    break
                if not isinstance(is_visible, bool):
                    raise DicAuthUiChangedError(stage)
                if not is_visible:
                    continue
                if allow_multiple:
                    self._raise_if_deadline_reached(
                        deadline,
                        stage,
                        completion=completion_deadline,
                    )
                    return item
                if visible is not None:
                    raise DicAuthUiChangedError(stage)
                visible = item
            if probe_failed:
                if strict_probe_errors:
                    raise DicAuthUiChangedError(stage)
                raise _AuthProbePending
            if visible is not None:
                self._raise_if_deadline_reached(
                    deadline,
                    stage,
                    completion=completion_deadline,
                )
                return visible
        self._raise_if_deadline_reached(
            deadline,
            stage,
            completion=completion_deadline,
        )
        return None

    async def _captcha_guard(
        self,
        stage: DicAuthStage,
        *,
        deadline: float,
        completion_deadline: bool = False,
    ) -> None:
        if (
            await self._candidate_visible(
                "auth.captcha",
                stage,
                deadline=deadline,
                allow_multiple=True,
                strict_probe_errors=True,
                completion_deadline=completion_deadline,
            )
            is not None
        ):
            raise DicAuthCaptchaRequiredError(stage)

    def _poll_route_is_allowed(
        self,
        stage: DicAuthStage,
        origin: str,
        path: str,
        pending_routes: tuple[tuple[str, str], ...],
    ) -> bool:
        try:
            self._raise_if_password_expired(self.page.url, stage)
            if self._is_exact_route(self.page.url, origin, path):
                return True
            if any(
                self._is_exact_route(self.page.url, pending_origin, pending_path)
                for pending_origin, pending_path in pending_routes
            ):
                return False
        except DicAuthPasswordExpiredError:
            raise
        except DicAuthenticationError:
            raise DicAuthUiChangedError(stage) from None
        raise DicAuthUiChangedError(stage)

    async def _wait_for_control(
        self,
        key: str,
        stage: DicAuthStage,
        *,
        origin: str,
        path: str,
        pending_routes: tuple[tuple[str, str], ...] = (),
    ) -> LocatorLike:
        deadline = self._control_deadline()
        while True:
            self._raise_if_deadline_reached(deadline, stage)
            on_target_route = self._poll_route_is_allowed(stage, origin, path, pending_routes)
            try:
                await self._captcha_guard(stage, deadline=deadline)
                if on_target_route:
                    control = await self._candidate_visible(
                        key,
                        stage,
                        deadline=deadline,
                    )
                    if control is not None:
                        self._raise_if_deadline_reached(deadline, stage)
                        return control
            except _AuthProbePending:
                pass
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DicAuthUiChangedError(stage)
            await self._sleep(min(_AUTH_POLL_SECONDS, remaining))

    def _control_deadline(self) -> float:
        control_deadline = self._monotonic() + self.login_timeout_seconds
        if self._flow_deadline is None:
            return control_deadline
        return min(control_deadline, self._flow_deadline)

    def _raise_if_deadline_reached(
        self,
        deadline: float,
        stage: DicAuthStage,
        *,
        completion: bool = False,
    ) -> None:
        if self._monotonic() < deadline:
            return
        if completion:
            raise DicAuthCompletionError(stage)
        raise DicAuthUiChangedError(stage)

    def _remaining_flow_seconds(self, stage: DicAuthStage) -> float:
        deadline = self._flow_deadline
        if deadline is None:
            raise DicAuthUiChangedError(stage)
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise DicAuthUiChangedError(stage)
        return remaining

    async def _fill_control(
        self,
        control: LocatorLike,
        value: str,
        stage: DicAuthStage,
    ) -> None:
        remaining = self._remaining_flow_seconds(stage)
        failed = False
        try:
            await asyncio.wait_for(control.fill(value), timeout=remaining)
        except Exception:
            failed = True
        if failed:
            raise DicAuthUiChangedError(stage)

    async def _click_control(
        self,
        control: LocatorLike,
        stage: DicAuthStage,
        *,
        outcome_unknown_on_failure: bool = False,
    ) -> None:
        remaining = self._remaining_flow_seconds(stage)
        failed = False
        cancelled = False
        try:
            await asyncio.wait_for(control.click(), timeout=remaining)
        except asyncio.CancelledError:
            if not outcome_unknown_on_failure:
                raise
            cancelled = True
        except Exception:
            failed = True
        if failed or cancelled:
            if outcome_unknown_on_failure:
                raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
            raise DicAuthUiChangedError(stage)

    async def _wait_for_auth_completion(self) -> None:
        stage = DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT
        deadline = self._control_deadline()
        while True:
            self._raise_if_deadline_reached(deadline, stage, completion=True)
            try:
                self._raise_if_password_expired(self.page.url, stage)
                if self._is_dic_app_route(self.page.url):
                    self._raise_if_deadline_reached(deadline, stage, completion=True)
                    return
                if not any(
                    self._is_exact_route(self.page.url, origin, path)
                    for origin, path in (
                        (_TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_SECRET_ENTRY_PATH),
                        (_DIC_ORIGIN, _DIC_AUTH_CALLBACK_PATH),
                    )
                ):
                    raise DicAuthUiChangedError(stage)
            except DicAuthPasswordExpiredError:
                raise
            except DicAuthUiChangedError:
                raise
            except DicAuthenticationError:
                raise DicAuthUiChangedError(stage) from None
            try:
                await self._captcha_guard(
                    stage,
                    deadline=deadline,
                    completion_deadline=True,
                )
                mfa = await self._candidate_visible(
                    "auth.mfa",
                    stage,
                    deadline=deadline,
                    allow_multiple=True,
                    strict_probe_errors=True,
                    completion_deadline=True,
                )
                if mfa is not None:
                    raise DicAuthMfaRequiredError(stage)
            except _AuthProbePending:
                pass
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DicAuthCompletionError(stage)
            await self._sleep(min(_AUTH_POLL_SECONDS, remaining))

    async def _complete_and_attest_post_submit(self) -> SessionStatus:
        await self._wait_for_auth_completion()
        if not await self._probe_authenticated_session():
            raise DicAuthenticationError("DIC login did not reach an authenticated route")
        self._authenticated_at = self._clock()
        return SessionStatus(
            state=SessionState.AUTHENTICATED,
            authenticated_at=self._authenticated_at,
        )

    async def _probe_authenticated_session_within_flow(self) -> bool:
        stage = DicAuthStage.SESSION_PROBE
        remaining = self._remaining_flow_seconds(stage)
        failed = False
        try:
            authenticated = await asyncio.wait_for(
                self._probe_authenticated_session(),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            raise
        except (DicAuthenticationError, DicAuthorizationError, DicConfigurationError):
            raise
        except Exception:
            failed = True
            authenticated = False
        if failed:
            raise DicAuthUiChangedError(stage)
        deadline = self._flow_deadline
        if deadline is None:
            raise DicAuthUiChangedError(stage)
        self._raise_if_deadline_reached(deadline, stage)
        return authenticated

    async def authenticate(self, credentials: DicCredentials) -> SessionStatus:
        self._flow_deadline = self._monotonic() + self.login_timeout_seconds
        try:
            return await self._authenticate_flow(credentials)
        finally:
            self._flow_deadline = None

    async def _authenticate_flow(self, credentials: DicCredentials) -> SessionStatus:
        if self.expected_tenant_id is None:
            raise DicConfigurationError("expected DIC tenant is not configured")
        try:
            await self.login_page.navigate()
        except Exception:
            raise DicAuthenticationError("DIC login navigation failed") from None
        self._raise_if_password_expired(self.page.url)

        if self._is_exact_route(self.page.url, _DIC_ORIGIN, "/it/login"):
            dic_email = await self._wait_for_control(
                "auth.dic_email",
                DicAuthStage.DIC_EMAIL,
                origin=_DIC_ORIGIN,
                path="/it/login",
            )
            await self._fill_control(dic_email, credentials.username, DicAuthStage.DIC_EMAIL)
            dic_submit = await self._wait_for_control(
                "auth.dic_submit",
                DicAuthStage.DIC_SUBMIT,
                origin=_DIC_ORIGIN,
                path="/it/login",
            )
            await self._click_control(dic_submit, DicAuthStage.DIC_SUBMIT)
        elif self._is_dic_app_route(self.page.url):
            if await self._probe_authenticated_session_within_flow():
                deadline = self._flow_deadline
                if deadline is None:
                    raise DicAuthUiChangedError(DicAuthStage.SESSION_PROBE)
                self._raise_if_deadline_reached(deadline, DicAuthStage.SESSION_PROBE)
                self._authenticated_at = self._clock()
                self._raise_if_deadline_reached(deadline, DicAuthStage.SESSION_PROBE)
                return SessionStatus(
                    state=SessionState.AUTHENTICATED,
                    authenticated_at=self._authenticated_at,
                )

        teamsystem_email = await self._wait_for_control(
            "auth.teamsystem_email",
            DicAuthStage.TEAMSYSTEM_EMAIL,
            origin=_TEAMSYSTEM_ORIGIN,
            path=_TEAMSYSTEM_EMAIL_PATH,
            pending_routes=((_DIC_ORIGIN, "/it/login"),),
        )
        await self._fill_control(
            teamsystem_email,
            credentials.username,
            DicAuthStage.TEAMSYSTEM_EMAIL,
        )
        teamsystem_email_submit = await self._wait_for_control(
            "auth.teamsystem_email_submit",
            DicAuthStage.TEAMSYSTEM_EMAIL_SUBMIT,
            origin=_TEAMSYSTEM_ORIGIN,
            path=_TEAMSYSTEM_EMAIL_PATH,
        )
        await self._click_control(
            teamsystem_email_submit,
            DicAuthStage.TEAMSYSTEM_EMAIL_SUBMIT,
        )

        teamsystem_password = await self._wait_for_control(
            "auth.teamsystem_password",
            DicAuthStage.TEAMSYSTEM_CREDENTIAL,
            origin=_TEAMSYSTEM_ORIGIN,
            path=_TEAMSYSTEM_SECRET_ENTRY_PATH,
            pending_routes=((_TEAMSYSTEM_ORIGIN, _TEAMSYSTEM_EMAIL_PATH),),
        )
        await self._fill_control(
            teamsystem_password,
            credentials.password.get_secret_value(),
            DicAuthStage.TEAMSYSTEM_CREDENTIAL,
        )
        teamsystem_password_submit = await self._wait_for_control(
            "auth.teamsystem_password_submit",
            DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT,
            origin=_TEAMSYSTEM_ORIGIN,
            path=_TEAMSYSTEM_SECRET_ENTRY_PATH,
        )
        await self._click_control(
            teamsystem_password_submit,
            DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT,
            outcome_unknown_on_failure=True,
        )
        outcome_unknown = False
        authenticated_status: SessionStatus | None = None
        try:
            remaining = self._remaining_flow_seconds(DicAuthStage.CREDENTIAL_SUBMIT)
            authenticated_status = await asyncio.wait_for(
                self._complete_and_attest_post_submit(),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            outcome_unknown = True
        except (
            DicAuthCaptchaRequiredError,
            DicAuthMfaRequiredError,
            DicAuthPasswordExpiredError,
        ):
            raise
        except DicAuthOutcomeUnknownError:
            raise
        except Exception:
            outcome_unknown = True
        if outcome_unknown:
            raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
        if authenticated_status is None:
            raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
        return authenticated_status


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
