from __future__ import annotations

import json

from typer.testing import CliRunner

from bh_dic.cli import app

runner = CliRunner()


def test_cli_version_and_mock_config_are_operational() -> None:
    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "0.2.1"

    config = runner.invoke(app, ["validate-config", "--mock"])
    assert config.exit_code == 0
    summary = json.loads(config.stdout)
    assert summary["mock_mode"] is True
    assert summary["model_provider"] == "openai"
    assert summary["model_store"] is False
    assert summary["openai_store"] is False
    assert summary["write_actions"] is False


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
