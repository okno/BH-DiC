from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import bh_dic.runtime as runtime_module
from bh_dic import cli
from bh_dic.config import AppSettings
from bh_dic.dic.auth import (
    DicAuthCaptchaRequiredError,
    DicAuthCompletionError,
    DicAuthMfaRequiredError,
    DicAuthOutcomeUnknownError,
    DicAuthStage,
    DicAuthUiChangedError,
    PlaywrightAuthenticator,
)
from bh_dic.dic.errors import DicPasswordExpiredError
from bh_dic.dic.models import SessionState, SessionStatus, StoredBrowserSession
from bh_dic.dic.session_vault import FernetSessionVault

runner = CliRunner()


def _settings(tmp_path: Path, *, session_key: str = "S" * 32) -> AppSettings:
    return AppSettings(
        app_env="production",
        mock_mode=False,
        audit_hmac_key="A" * 32,
        encryption_key="E" * 32,
        discord_bot_token="synthetic-discord-token",
        discord_application_id=100000000000000001,
        discord_guild_id=100000000000000002,
        discord_channel_id=100000000000000003,
        openai_api_key="synthetic-openai-key",
        openai_model="synthetic-model",
        dic_username="operator@example.invalid",
        dic_password="synthetic-password",
        dic_session_encryption_key=session_key,
        dic_expected_tenant_id="123456789",
        data_dir=tmp_path,
        dic_session_state_path=tmp_path / "session" / "dic_session.enc",
        _env_file=None,
    )


def _save_session(settings: AppSettings, *, expired: bool = False) -> None:
    now = datetime.now(UTC)
    session = StoredBrowserSession(
        storage_state={
            "cookies": [
                {
                    "name": "synthetic-session",
                    "value": "COOKIE-MUST-NOT-BE-PRINTED",
                    "domain": "example.invalid",
                    "path": "/",
                }
            ],
            "origins": [],
        },
        authenticated_at=now - timedelta(hours=2) if expired else now,
        expires_at=now - timedelta(hours=1) if expired else now + timedelta(hours=1),
        account_hint_redacted="o***@example.invalid",
    )
    key = settings.dic_session_encryption_key
    assert key is not None
    FernetSessionVault(settings.dic_session_state_path.resolve(), key.get_secret_value()).save(
        session
    )


def test_dic_auth_check_defaults_to_safe_offline_vault_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    _save_session(settings)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    build_runtime = AsyncMock(side_effect=AssertionError("live runtime must not start"))
    monkeypatch.setattr(cli, "build_runtime", build_runtime)

    result = runner.invoke(cli.app, ["dic-auth-check"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["authentication"] == "UNVERIFIED_OFFLINE"
    assert payload["configuration"] == "VALID"
    assert payload["live_contacted"] is False
    assert payload["mode"] == "offline"
    assert payload["session"] == "ENCRYPTED_NON_EXPIRED"
    assert payload["tenant_binding"] == "UNVERIFIED_OFFLINE"
    assert payload["tenant_configured"] is True
    assert payload["vault_permissions"] in {"PRIVATE", "UNVERIFIED_NON_POSIX"}
    build_runtime.assert_not_awaited()
    assert "COOKIE-MUST-NOT-BE-PRINTED" not in result.output
    assert "123456789" not in result.output
    assert "synthetic-password" not in result.output
    assert str(settings.dic_session_state_path) not in result.output


def test_dic_auth_check_rejects_a_symlink_before_resolving_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    _save_session(settings)
    configured_path = settings.dic_session_state_path.expanduser()
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == configured_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)

    result = runner.invoke(cli.app, ["dic-auth-check"])

    assert result.exit_code == 1
    assert "DicSessionVaultError" in result.output
    assert str(configured_path) not in result.output


@pytest.mark.parametrize("failure", ["missing", "wrong-key", "expired", "tenant-missing"])
def test_dic_auth_check_offline_fails_closed_without_sensitive_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    settings = _settings(tmp_path)
    if failure != "missing":
        _save_session(settings, expired=failure == "expired")
    if failure == "wrong-key":
        settings = settings.model_copy(update={"dic_session_encryption_key": SecretStr("W" * 32)})
    elif failure == "tenant-missing":
        settings = settings.model_copy(update={"dic_expected_tenant_id": None})
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)

    result = runner.invoke(cli.app, ["dic-auth-check"])

    assert result.exit_code == 1
    assert "COOKIE-MUST-NOT-BE-PRINTED" not in result.output
    assert "123456789" not in result.output
    assert str(settings.dic_session_state_path) not in result.output


@pytest.mark.asyncio
async def test_live_helper_uses_adapter_status_and_always_closes_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    adapter = SimpleNamespace(
        session_status=AsyncMock(return_value=SessionStatus(state=SessionState.AUTHENTICATED))
    )
    runtime = SimpleNamespace(adapter=adapter, close=AsyncMock())
    build_runtime = AsyncMock(return_value=runtime)
    monkeypatch.setattr(cli, "build_runtime", build_runtime)

    result = await cli._dic_auth_check_live(settings)

    assert result["authentication"] == "LIVE_AUTHENTICATED"
    assert result["tenant_binding"] == "VERIFIED_BY_ADAPTER"
    build_runtime.assert_awaited_once_with(settings, authenticate_dic=True)
    adapter.session_status.assert_awaited_once_with()
    runtime.close.assert_awaited_once_with()


def test_live_flag_is_explicit_and_can_be_tested_without_external_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    live_check = AsyncMock(
        return_value={
            "authentication": "LIVE_AUTHENTICATED",
            "configuration": "VALID",
            "live_contacted": True,
            "mode": "live",
            "session": "AUTHENTICATED",
            "tenant_binding": "VERIFIED_BY_ADAPTER",
            "tenant_configured": True,
        }
    )
    monkeypatch.setattr(cli, "_dic_auth_check_live", live_check)

    result = runner.invoke(cli.app, ["dic-auth-check", "--live"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["mode"] == "live"
    live_check.assert_awaited_once_with(settings)


def test_live_failure_outputs_only_closed_stage_and_error_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    live_check = AsyncMock(side_effect=DicAuthUiChangedError(DicAuthStage.DIC_EMAIL))
    monkeypatch.setattr(cli, "_dic_auth_check_live", live_check)

    result = runner.invoke(cli.app, ["dic-auth-check", "--live"])

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error_type": "DicAuthUiChangedError",
        "stage": "DIC_EMAIL",
    }


def test_live_unknown_outcome_uses_stable_nonrestartable_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(
        cli,
        "_dic_auth_check_live",
        AsyncMock(side_effect=DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)),
    )

    result = runner.invoke(cli.app, ["dic-auth-check", "--live"])

    assert result.exit_code == cli.DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE == 78
    assert json.loads(result.stderr) == {
        "error_type": "DicAuthOutcomeUnknownError",
        "stage": "CREDENTIAL_SUBMIT",
    }


@pytest.mark.parametrize(
    ("failure", "expected_stage"),
    [
        (DicPasswordExpiredError("private-startup-auth-marker"), "UNCLASSIFIED"),
        (DicAuthUiChangedError(DicAuthStage.DIC_EMAIL), "DIC_EMAIL"),
        (DicAuthCaptchaRequiredError(DicAuthStage.DIC_EMAIL), "DIC_EMAIL"),
        (
            DicAuthMfaRequiredError(DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT),
            "TEAMSYSTEM_CREDENTIAL_SUBMIT",
        ),
        (
            DicAuthCompletionError(DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT),
            "TEAMSYSTEM_CREDENTIAL_SUBMIT",
        ),
        (
            DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT),
            "CREDENTIAL_SUBMIT",
        ),
    ],
)
def test_run_uses_nonrestartable_exit_for_every_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Exception,
    expected_stage: str,
) -> None:
    settings = _settings(tmp_path)
    sensitive = "private-startup-auth-marker"
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_run_gateway", AsyncMock(side_effect=failure))

    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == cli.DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE == 78
    assert json.loads(result.stderr) == {
        "error_type": type(failure).__name__,
        "stage": expected_stage,
    }
    assert sensitive not in result.output


def test_password_click_failure_flows_through_runtime_cleanup_to_live_auth_exit_78(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    sensitive = "private-integrated-click-marker"
    events: list[str] = []

    class FailingPasswordSubmit:
        clicks = 0

        async def click(self) -> None:
            self.clicks += 1
            raise RuntimeError(sensitive)

    submit = FailingPasswordSubmit()

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            return None

        async def persist(self, _session: object) -> None:
            raise AssertionError("unknown outcome must not be persisted")

    class FakeBrowser:
        def __init__(self, _options: object) -> None:
            pass

        async def start(self, _state: object, *, session_storage: object = None) -> object:
            assert session_storage is None
            return object()

        async def close(self) -> None:
            events.append("browser-close")

    class FakeLiveAdapter:
        def __init__(self, _page: object, **_kwargs: object) -> None:
            pass

        async def ensure_authenticated(self, _credentials: object) -> None:
            auth = PlaywrightAuthenticator(  # type: ignore[arg-type]
                object(),
                "https://secure.dipendentincloud.it",
                expected_tenant_id="123456789",
            )
            auth._flow_deadline = time.monotonic() + 5
            await auth._click_control(
                submit,  # type: ignore[arg-type]
                DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT,
                outcome_unknown_on_failure=True,
            )

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    async def live_check(_settings: AppSettings) -> dict[str, object]:
        await runtime_module._adapter(
            _settings,
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )
        raise AssertionError("authentication failure must stop the live check")

    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(cli, "_dic_auth_check_live", live_check)

    result = runner.invoke(cli.app, ["dic-auth-check", "--live"])

    assert result.exit_code == cli.DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE == 78
    assert json.loads(result.stderr) == {
        "error_type": "DicAuthOutcomeUnknownError",
        "stage": "CREDENTIAL_SUBMIT",
    }
    assert submit.clicks == 1
    assert events == ["browser-close"]
    assert sensitive not in result.output


def test_live_unclassified_failure_never_outputs_exception_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    sensitive = "private-provider-message"
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(
        cli,
        "_dic_auth_check_live",
        AsyncMock(side_effect=RuntimeError(sensitive)),
    )

    result = runner.invoke(cli.app, ["dic-auth-check", "--live"])

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error_type": "RuntimeError",
        "stage": "UNCLASSIFIED",
    }
    assert sensitive not in result.output


def test_configuration_failure_uses_same_private_unclassified_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive = "private-configuration-message"
    monkeypatch.setattr(
        cli,
        "_settings",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError(sensitive)),
    )

    result = runner.invoke(cli.app, ["dic-auth-check"])

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error_type": "ValueError",
        "stage": "UNCLASSIFIED",
    }
    assert sensitive not in result.output
