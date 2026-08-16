from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import bh_dic.cli as cli
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database
from bh_dic.files.antivirus import AntivirusResult, AntivirusVerdict
from bh_dic.files.models import UploadRecord, UploadStatus
from bh_dic.files.repository import SqlAlchemyUploadRepository

runner = CliRunner()
UPLOAD_ID = "a" * 32


def _settings(tmp_path: Path | None = None, **updates: object) -> AppSettings:
    base = AppSettings(
        app_env="mock",
        mock_mode=True,
        database_url="sqlite+aiosqlite:///:memory:",
        discord_application_id=103,
        discord_guild_id=101,
        discord_channel_id=102,
        discord_hr_read_role_ids=(201,),
        _env_file=None,
    )
    if tmp_path is not None:
        updates = {
            "data_dir": tmp_path,
            "log_dir": tmp_path / "log",
            "dic_session_state_path": tmp_path / "session" / "dic_session.enc",
            **updates,
        }
    return base.model_copy(update=updates)


def _upload_record(*, bucket: str | None = "clean") -> UploadRecord:
    now = datetime.now(UTC)
    return UploadRecord(
        upload_id=UPLOAD_ID,
        original_filename="[PROTECTED]",
        opaque_name=UPLOAD_ID,
        status=UploadStatus.CLEAN,
        bucket=bucket,
        claimed_mime="application/pdf",
        detected_mime="application/pdf",
        size_bytes=7,
        sha256="b" * 64,
        antivirus_status="CLEAN",
        rejection_reason=None,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


def test_config_failure_and_both_init_db_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenSettings:
        def __init__(self) -> None:
            raise ValueError("synthetic invalid configuration")

    monkeypatch.setattr(cli, "AppSettings", BrokenSettings)
    invalid = runner.invoke(cli.app, ["validate-config"])
    assert invalid.exit_code == 1
    assert "Configurazione non valida" in invalid.output

    monkeypatch.setattr(cli, "AppSettings", AppSettings)
    mock = runner.invoke(cli.app, ["init-db", "--mock"])
    assert mock.exit_code == 0, mock.output
    assert json.loads(mock.stdout) == {"database": "initialized", "mock": True}

    migrations = AsyncMock()
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: _settings())
    monkeypatch.setattr(cli, "run_migrations_async", migrations)
    normal = runner.invoke(cli.app, ["init-db"])
    assert normal.exit_code == 0, normal.output
    migrations.assert_awaited_once_with("sqlite+aiosqlite:///:memory:")


@pytest.mark.asyncio
async def test_model_check_uses_only_the_closed_synthetic_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, frozenset[str]]] = []

    class FakeClient:
        async def route(
            self, request: str, allowed_function_ids: frozenset[str]
        ) -> SimpleNamespace:
            calls.append((request, allowed_function_ids))
            return SimpleNamespace(
                envelope=SimpleNamespace(function_id="UNSUPPORTED"),
                metadata=SimpleNamespace(
                    provider="groq",
                    model="openai/gpt-oss-120b",
                    tool_name="unsupported_request",
                ),
            )

    settings = _settings().model_copy(update={"mock_mode": False, "model_provider": "groq"})
    monkeypatch.setattr(cli, "build_intent_client", lambda *_args, **_kwargs: FakeClient())
    result = await cli._model_check_live(settings)

    assert calls and calls[0][1] == frozenset()
    assert "dati personali" in calls[0][0]
    assert result == {
        "status": "LIVE_VERIFIED",
        "live_contacted": True,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "tool": "unsupported_request",
        "store": False,
        "tool_execution": False,
    }


def test_model_check_cli_is_offline_by_default_and_live_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings().model_copy(
        update={"model_provider": "groq", "groq_model": "openai/gpt-oss-120b"}
    )
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)

    offline = runner.invoke(cli.app, ["model-check"])
    assert offline.exit_code == 0, offline.output
    payload = json.loads(offline.stdout)
    assert payload["status"] == "UNVERIFIED_OFFLINE"
    assert payload["provider"] == "groq"
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["live_contacted"] is False

    rejected = runner.invoke(cli.app, ["model-check", "--live"])
    assert rejected.exit_code == 2
    assert "MOCK_MODE" in rejected.output

    live_settings = settings.model_copy(update={"mock_mode": False})
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: live_settings)
    monkeypatch.setattr(
        cli,
        "_model_check_live",
        AsyncMock(
            return_value={
                "status": "LIVE_VERIFIED",
                "live_contacted": True,
                "provider": "groq",
            }
        ),
    )
    verified = runner.invoke(cli.app, ["model-check", "--live"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout)["status"] == "LIVE_VERIFIED"

    monkeypatch.setattr(
        cli, "_model_check_live", AsyncMock(side_effect=RuntimeError("private detail"))
    )
    failed = runner.invoke(cli.app, ["model-check", "--live"])
    assert failed.exit_code == 1
    assert "RuntimeError" in failed.output
    assert "private detail" not in failed.output


@pytest.mark.asyncio
async def test_mock_smoke_failure_guards_always_close_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTree:
        def __init__(self) -> None:
            self.commands: list[SimpleNamespace] = []

        def get_commands(self, **_kwargs: object) -> list[SimpleNamespace]:
            return self.commands

    def fake_runtime(result: object, commands: list[SimpleNamespace]) -> SimpleNamespace:
        tree = FakeTree()
        tree.commands = commands
        return SimpleNamespace(
            database=SimpleNamespace(create_schema=AsyncMock()),
            bot=SimpleNamespace(
                setup_hook=AsyncMock(),
                tree=tree,
                allowed_guild=object(),
            ),
            coordinator=SimpleNamespace(ask=AsyncMock(return_value=result)),
            close=AsyncMock(),
        )

    bad_result = SimpleNamespace(
        success=False,
        sensitivity=SimpleNamespace(value="SENSITIVE"),
        description="bad",
    )
    first = fake_runtime(bad_result, [SimpleNamespace(name="bh")])
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=first))
    with pytest.raises(RuntimeError, match="vertical slice"):
        await cli._mock_smoke(_settings())
    first.close.assert_awaited_once_with()

    good_result = SimpleNamespace(
        success=True,
        sensitivity=SimpleNamespace(value="PUBLIC_AGGREGATE"),
        description="ok",
    )
    second = fake_runtime(good_result, [])
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=second))
    with pytest.raises(RuntimeError, match="command group"):
        await cli._mock_smoke(_settings())
    second.close.assert_awaited_once_with()


def test_smoke_wrapper_redacts_internal_failure_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_mock_smoke", AsyncMock(side_effect=RuntimeError("secret detail")))
    failed = runner.invoke(cli.app, ["smoke-mock"])
    assert failed.exit_code == 1
    assert "RuntimeError" in failed.output
    assert "secret detail" not in failed.output


@pytest.mark.asyncio
async def test_gateway_and_registration_lifecycles_close_on_every_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrations = AsyncMock()
    monkeypatch.setattr(cli, "run_migrations_async", migrations)

    missing_runtime = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=missing_runtime))
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        await cli._run_gateway(_settings())
    missing_runtime.close.assert_awaited_once_with()

    token_settings = _settings().model_copy(
        update={"discord_bot_token": SecretStr("synthetic-token")}
    )
    gateway_runtime = SimpleNamespace(
        bot=SimpleNamespace(start=AsyncMock()),
        close=AsyncMock(),
    )
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=gateway_runtime))
    await cli._run_gateway(token_settings)
    gateway_runtime.bot.start.assert_awaited_once_with("synthetic-token", reconnect=True)
    gateway_runtime.close.assert_awaited_once_with()

    register_runtime = SimpleNamespace(
        bot=SimpleNamespace(
            login=AsyncMock(),
            tree=SimpleNamespace(sync=AsyncMock(return_value=[object(), object()])),
            allowed_guild=object(),
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=register_runtime))
    assert await cli._register(token_settings) == 2
    register_runtime.bot.login.assert_awaited_once_with("synthetic-token")
    register_runtime.close.assert_awaited_once_with()

    no_token_registration = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=no_token_registration))
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        await cli._register(_settings())
    no_token_registration.close.assert_awaited_once_with()


def test_run_and_register_wrappers_handle_success_interrupt_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: _settings())

    monkeypatch.setattr(cli, "_run_gateway", AsyncMock(return_value=None))
    assert runner.invoke(cli.app, ["run", "--mock"]).exit_code == 0

    monkeypatch.setattr(cli, "_run_gateway", AsyncMock(side_effect=KeyboardInterrupt))
    interrupted = runner.invoke(cli.app, ["run", "--mock"])
    assert interrupted.exit_code == 0
    assert "Arresto richiesto" in interrupted.output

    monkeypatch.setattr(cli, "_run_gateway", AsyncMock(side_effect=RuntimeError("private")))
    failed = runner.invoke(cli.app, ["run", "--mock"])
    assert failed.exit_code == 1
    assert "RuntimeError" in failed.output
    assert "private" not in failed.output

    monkeypatch.setattr(cli, "_register", AsyncMock(return_value=3))
    registered = runner.invoke(cli.app, ["register-commands"])
    assert registered.exit_code == 0
    assert json.loads(registered.stdout)["registered_commands"] == 3

    monkeypatch.setattr(cli, "_register", AsyncMock(side_effect=RuntimeError("private")))
    registration_failed = runner.invoke(cli.app, ["register-commands"])
    assert registration_failed.exit_code == 1
    assert "RuntimeError" in registration_failed.output
    assert "private" not in registration_failed.output


def test_health_and_doctor_cover_local_success_and_redacted_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = runner.invoke(cli.app, ["health", "--mock"])
    assert health.exit_code == 0, health.output
    assert json.loads(health.stdout)["state"] == "healthy"

    monkeypatch.setattr(cli, "_with_database", AsyncMock(side_effect=RuntimeError("private")))
    failed = runner.invoke(cli.app, ["health", "--mock"])
    assert failed.exit_code == 1
    assert "RuntimeError" in failed.output
    assert "private" not in failed.output

    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: _settings())

    def resolve(host: str, *_args: object, **_kwargs: object) -> list[object]:
        if host == "api.openai.com":
            raise OSError("synthetic DNS failure")
        return [object()]

    monkeypatch.setattr(cli.socket, "getaddrinfo", resolve)
    doctor = runner.invoke(cli.app, ["doctor", "--mock", "--online"])
    assert doctor.exit_code == 0, doctor.output
    payload = json.loads(doctor.stdout)
    assert payload["dns"]["discord.com"] is True
    assert payload["dns"]["api.openai.com"] is False

    fake_sys = SimpleNamespace(version="3.11.0 synthetic", version_info=(3, 11))
    monkeypatch.setattr(cli, "sys", fake_sys)
    unsupported = runner.invoke(cli.app, ["doctor", "--mock"])
    assert unsupported.exit_code == 1
    assert json.loads(unsupported.stdout)["python_supported"] is False


class FakeAuditResult:
    def __init__(self, valid: bool) -> None:
        self.valid = valid

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"valid": self.valid, "event_count": 0, "last_sequence": 0}


class FakeAuditService:
    result = FakeAuditResult(True)

    def __init__(self, _database: Database, _key: str) -> None:
        pass

    async def verify(self) -> FakeAuditResult:
        return self.result


def test_audit_and_session_commands_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: _settings())
    missing_audit = runner.invoke(cli.app, ["audit-verify"])
    assert missing_audit.exit_code == 1
    assert "AUDIT_HMAC_KEY" in missing_audit.output

    audit_settings = _settings().model_copy(update={"audit_hmac_key": SecretStr("A" * 32)})
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: audit_settings)
    monkeypatch.setattr(cli, "AuditService", FakeAuditService)
    verified = runner.invoke(cli.app, ["audit-verify"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout)["valid"] is True

    FakeAuditService.result = FakeAuditResult(False)
    invalid = runner.invoke(cli.app, ["audit-verify"])
    assert invalid.exit_code == 1
    assert "RuntimeError" in invalid.output

    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: _settings(tmp_path))
    missing_session_key = runner.invoke(cli.app, ["invalidate-session"])
    assert missing_session_key.exit_code == 1

    session_settings = _settings(tmp_path).model_copy(
        update={"dic_session_encryption_key": SecretStr("S" * 32)}
    )
    session_settings.dic_session_state_path.parent.mkdir(parents=True)
    session_settings.dic_session_state_path.write_bytes(b"encrypted-placeholder")
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: session_settings)
    invalidated = runner.invoke(cli.app, ["invalidate-session"])
    assert invalidated.exit_code == 0, invalidated.output
    assert json.loads(invalidated.stdout)["encrypted_session_invalidated"] is True
    assert not session_settings.dic_session_state_path.exists()


@pytest.mark.asyncio
async def test_file_repository_helper_disposes_database() -> None:
    seen: list[Database] = []

    async def operation(_repository: SqlAlchemyUploadRepository, database: Database) -> str:
        seen.append(database)
        return "ok"

    assert await cli._file_repository(_settings(), operation) == "ok"
    assert seen and seen[0].engine.sync_engine.pool.status()


def test_file_commands_render_metadata_scan_and_purge_without_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _upload_record()

    class FakeRepository:
        current: UploadRecord | None = record

        def __init__(self, _sessions: object) -> None:
            pass

        async def list_records(self) -> tuple[UploadRecord, ...]:
            return () if self.current is None else (self.current,)

        async def get(self, _upload_id: str) -> UploadRecord | None:
            return self.current

    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(cli, "SqlAlchemyUploadRepository", FakeRepository)

    listed = runner.invoke(cli.app, ["files", "list"])
    assert listed.exit_code == 0, listed.output
    listed_payload = json.loads(listed.stdout)
    assert listed_payload[0]["upload_id"] == UPLOAD_ID
    assert "original_filename" not in listed_payload[0]

    metadata = runner.invoke(cli.app, ["files", "metadata", UPLOAD_ID])
    assert metadata.exit_code == 0, metadata.output
    metadata_payload = json.loads(metadata.stdout)
    assert metadata_payload["sha256"] == "b" * 64
    assert "original_filename" not in metadata_payload

    clean_path = settings.data_dir / "uploads" / "clean" / UPLOAD_ID
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    clean_path.write_bytes(b"content")

    class FakeScanner:
        def __init__(self, _endpoint: str | None) -> None:
            pass

        def scan(self, path: Path) -> AntivirusResult:
            assert path == clean_path.resolve()
            return AntivirusResult(AntivirusVerdict.CLEAN, "synthetic")

    monkeypatch.setattr(cli, "ClamAVScanner", FakeScanner)
    scanned = runner.invoke(cli.app, ["files", "scan", UPLOAD_ID])
    assert scanned.exit_code == 0, scanned.output
    assert json.loads(scanned.stdout)["antivirus_verdict"] == "CLEAN"

    class FakeRetention:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def purge_expired(self) -> tuple[str, ...]:
            return (UPLOAD_ID,)

    monkeypatch.setattr(cli, "FileRetentionService", FakeRetention)
    purged = runner.invoke(cli.app, ["files", "purge-expired"])
    assert purged.exit_code == 0, purged.output
    assert json.loads(purged.stdout)["purged_upload_ids"] == [UPLOAD_ID]

    FakeRepository.current = None
    missing_metadata = runner.invoke(cli.app, ["files", "metadata", UPLOAD_ID])
    assert missing_metadata.exit_code == 1
    assert "KeyError" in missing_metadata.output
    missing_scan = runner.invoke(cli.app, ["files", "scan", UPLOAD_ID])
    assert missing_scan.exit_code == 1
    assert "KeyError" in missing_scan.output

    FakeRepository.current = _upload_record(bucket=None)
    bucketless = runner.invoke(cli.app, ["files", "scan", UPLOAD_ID])
    assert bucketless.exit_code == 1
    assert "KeyError" in bucketless.output
