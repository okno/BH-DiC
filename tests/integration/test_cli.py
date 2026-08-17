from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bh_dic import cli
from bh_dic.cli import app
from bh_dic.config import AppSettings

runner = CliRunner()


def test_cli_version_and_mock_config_are_operational() -> None:
    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "0.3.0"

    config = runner.invoke(app, ["validate-config", "--mock"])
    assert config.exit_code == 0
    summary = json.loads(config.stdout)
    assert summary["mock_mode"] is True
    assert summary["model_provider"] == "openai"
    assert summary["model_store"] is False
    assert summary["openai_store"] is False
    assert summary["write_actions"] is False


def test_mock_config_ignores_operator_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "MODEL_PROVIDER=groq",
                "GROQ_API_KEY=synthetic-operator-groq-key",
                "DISCORD_BOT_TOKEN=x",
                f"AUDIT_HMAC_KEY={'A' * 32}",
                "DATABASE_URL=sqlite+aiosqlite:///./operator.sqlite3",
                "DIC_USERNAME=operator@example.invalid",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(AppSettings.model_config, "env_file", ".env")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "40")

    settings = cli._settings(mock=True)

    assert settings.model_provider == "openai"
    assert settings.model_timeout_seconds == settings.openai_timeout_seconds == 60
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.audit_hmac_key is None
    assert settings.discord_bot_token is None
    assert settings.groq_api_key is None
    assert settings.dic_username is None


def test_cli_mock_smoke_builds_vertical_slice_without_gateway() -> None:
    result = runner.invoke(app, ["smoke-mock"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["discord_gateway_started"] is False
    assert payload["writes_enabled"] is False


def test_check_only_requires_explicit_mock_mode() -> None:
    result = runner.invoke(app, ["run", "--check-only"])
    assert result.exit_code == 2
    assert "richiede --mock" in result.output
