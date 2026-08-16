from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from bh_dic import cli
from bh_dic.config import AppSettings
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
    build_runtime.assert_awaited_once_with(settings)
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
