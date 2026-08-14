from __future__ import annotations

import pytest
from pydantic import ValidationError

from bh_dic.config import AppSettings


def valid_runtime_values() -> dict[str, object]:
    return {
        "app_env": "production",
        "mock_mode": False,
        "audit_hmac_key": "a" * 32,
        "encryption_key": "b" * 32,
        "discord_bot_token": "synthetic-discord-token",
        "discord_application_id": 100000000000000001,
        "discord_guild_id": 100000000000000002,
        "discord_channel_id": 100000000000000003,
        "openai_api_key": "synthetic-openai-key",
        "openai_model": "configured-model-name",
        "dic_username": "synthetic@example.invalid",
        "dic_password": "synthetic-password-value",
        "dic_session_encryption_key": "c" * 32,
        "dic_expected_tenant_id": "tenant-synthetic-001",
    }


def test_missing_runtime_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Missing required runtime configuration"):
        AppSettings(
            app_env="production",
            mock_mode=False,
            discord_guild_id=None,
            _env_file=None,
        )


def test_non_production_environment_does_not_bypass_required_secrets() -> None:
    with pytest.raises(ValidationError, match="Missing required runtime configuration"):
        AppSettings(app_env="test", mock_mode=False, _env_file=None)


def test_explicit_mock_mode_allows_isolated_tests() -> None:
    settings = AppSettings(app_env="test", mock_mode=True, _env_file=None)

    assert settings.mock_mode is True
    assert settings.openai_store is False
    assert settings.enable_read_actions is True
    assert settings.enable_write_actions is False
    assert not any(settings.write_flags.values())


def test_mock_mode_is_forbidden_in_production_environment() -> None:
    with pytest.raises(ValidationError, match="MOCK_MODE may only be used"):
        AppSettings(app_env="production", mock_mode=True, _env_file=None)


def test_openai_provider_storage_is_always_rejected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_STORE=true is forbidden"):
        AppSettings(app_env="test", mock_mode=True, openai_store=True, _env_file=None)


def test_dic_base_url_cannot_turn_the_adapter_into_a_general_browser() -> None:
    with pytest.raises(ValidationError, match=r"secure\.dipendentincloud\.it"):
        AppSettings(
            app_env="test",
            mock_mode=True,
            dic_base_url="https://example.invalid/arbitrary",
            _env_file=None,
        )


def test_complete_runtime_configuration_is_accepted_and_redacted() -> None:
    settings = AppSettings(**valid_runtime_values(), _env_file=None)

    summary = settings.safe_summary()
    assert summary["discord_guild_configured"] is True
    assert summary["dic_expected_tenant_configured"] is True
    assert "synthetic-discord-token" not in repr(summary)
    assert "synthetic-openai-key" not in repr(summary)


def test_discord_role_ids_are_deduplicated_and_validated() -> None:
    settings = AppSettings(
        app_env="test",
        mock_mode=True,
        discord_hr_read_role_ids="3, 2, 3",
        discord_balance_role_ids="8, 9",
        _env_file=None,
    )
    assert settings.discord_hr_read_role_ids == (3, 2)
    assert settings.discord_balance_role_ids == (8, 9)

    with pytest.raises(ValidationError, match="positive integers"):
        AppSettings(
            app_env="test",
            mock_mode=True,
            discord_hr_read_role_ids="3,0",
            _env_file=None,
        )


def test_specific_write_flag_requires_global_kill_switch() -> None:
    with pytest.raises(ValidationError, match="ENABLE_WRITE_ACTIONS=true"):
        AppSettings(
            app_env="test",
            mock_mode=True,
            enable_employee_update=True,
            _env_file=None,
        )


def test_live_write_test_requires_dedicated_target_and_confirmed_tenant() -> None:
    with pytest.raises(ValidationError, match="DIC_TEST_EMPLOYEE_ID"):
        AppSettings(
            app_env="test",
            mock_mode=True,
            enable_write_actions=True,
            enable_live_write_tests=True,
            _env_file=None,
        )


def test_critical_writes_require_two_person_approval() -> None:
    with pytest.raises(ValidationError, match="REQUIRE_TWO_PERSON_APPROVAL=true"):
        AppSettings(
            app_env="test",
            mock_mode=True,
            enable_write_actions=True,
            enable_employee_delete=True,
            require_two_person_approval=False,
            _env_file=None,
        )


def test_expected_tenant_is_required_in_normal_runtime() -> None:
    values = valid_runtime_values()
    values["dic_expected_tenant_id"] = None
    with pytest.raises(ValidationError, match="DIC_EXPECTED_TENANT_ID"):
        AppSettings(**values, _env_file=None)


@pytest.mark.parametrize("placeholder", ["CHANGEME", "<INSERT_TOKEN>", "placeholder"])
def test_runtime_placeholders_are_rejected(placeholder: str) -> None:
    values = valid_runtime_values()
    values["discord_bot_token"] = placeholder
    with pytest.raises(ValidationError, match="DISCORD_BOT_TOKEN contains a placeholder"):
        AppSettings(**values, _env_file=None)
