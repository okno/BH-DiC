"""Typer command line interface used by operators and shell wrappers."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, Never
from urllib.parse import SplitResult, urlsplit

import typer

from bh_dic import __version__
from bh_dic.audit.service import AuditService
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database
from bh_dic.database.migrations import run_migrations_async
from bh_dic.dic.auth import DicAuthOutcomeUnknownError, DicAuthStage
from bh_dic.dic.errors import DicAuthenticationError
from bh_dic.dic.models import SessionState, StoredBrowserSession
from bh_dic.dic.session_vault import FernetSessionVault, resolve_session_vault_path
from bh_dic.discord.checks import DiscordActor
from bh_dic.files.antivirus import ClamAVScanner
from bh_dic.files.quarantine import QuarantineStore
from bh_dic.files.repository import SqlAlchemyUploadRepository
from bh_dic.files.retention import FileRetentionService
from bh_dic.health import HealthChecker
from bh_dic.logging import configure_logging
from bh_dic.openai.factory import build_intent_client
from bh_dic.openai.intent_router import OpenAIIntentRouter
from bh_dic.openai.prompts import build_intent_router_prompt
from bh_dic.openai.providers import (
    GROQ_OPENAI_BASE_URL,
    OPENAI_RESPONSES_BASE_URL,
    llama_endpoint_is_loopback,
)
from bh_dic.policies.roles import LogicalRole
from bh_dic.runtime import build_runtime

app = typer.Typer(
    name="bh-dic",
    help="BH-DiC secure single-node operator CLI.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
files_app = typer.Typer(help="Inspect quarantined file metadata without printing content.")
app.add_typer(files_app, name="files")

DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE = 78


def _run[T](awaitable: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(awaitable)


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _fail(message: str, *, code: int = 1) -> Never:
    typer.echo(message, err=True)
    raise typer.Exit(code)


def _dic_auth_failure(exc: Exception, *, code: int | None = None) -> Never:
    stage = getattr(exc, "stage", DicAuthStage.UNCLASSIFIED)
    if not isinstance(stage, DicAuthStage):
        stage = DicAuthStage.UNCLASSIFIED
    _fail(
        json.dumps(
            {
                "error_type": type(exc).__name__,
                "stage": stage.value,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        code=(
            code
            if code is not None
            else (
                DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE
                if isinstance(exc, DicAuthOutcomeUnknownError)
                else 1
            )
        ),
    )


def _validate_local_dic_session(session: StoredBrowserSession) -> None:
    """Validate only the shape needed to safely reuse Playwright storage state."""

    cookies = session.storage_state.get("cookies")
    origins = session.storage_state.get("origins")
    if not isinstance(cookies, list) or not all(isinstance(item, dict) for item in cookies):
        raise ValueError("encrypted browser session has invalid cookie metadata")
    if not isinstance(origins, list) or not all(isinstance(item, dict) for item in origins):
        raise ValueError("encrypted browser session has invalid origin metadata")


def _dic_auth_check_offline(settings: AppSettings) -> dict[str, object]:
    """Inspect local encrypted session readiness without starting a browser or using network."""

    if settings.mock_mode:
        raise ValueError("DIC authentication verification is unavailable in mock mode")
    key = settings.dic_session_encryption_key
    if key is None:
        raise ValueError("DIC session encryption key is not configured")
    if settings.dic_expected_tenant_id is None:
        raise ValueError("expected DIC tenant is not configured")

    session_path = resolve_session_vault_path(settings.dic_session_state_path, settings.data_dir)
    if not session_path.is_file():
        raise ValueError("encrypted DIC session vault is missing or unsafe")
    permission_state = "UNVERIFIED_NON_POSIX"
    if os.name == "posix":
        if session_path.stat().st_mode & 0o077:
            raise PermissionError("encrypted DIC session vault must not be group/world accessible")
        permission_state = "PRIVATE"

    session = FernetSessionVault(session_path, key.get_secret_value()).load()
    if session is None:
        raise ValueError("encrypted DIC session vault is empty")
    _validate_local_dic_session(session)
    return {
        "authentication": "UNVERIFIED_OFFLINE",
        "configuration": "VALID",
        "live_contacted": False,
        "mode": "offline",
        "session": "ENCRYPTED_NON_EXPIRED",
        "tenant_binding": "UNVERIFIED_OFFLINE",
        "tenant_configured": True,
        "vault_permissions": permission_state,
    }


async def _dic_auth_check_live(settings: AppSettings) -> dict[str, object]:
    """Explicitly build the live adapter and verify its tenant-bound session."""

    runtime = await build_runtime(settings, authenticate_dic=True)
    try:
        status = await runtime.adapter.session_status()
        if status.state is not SessionState.AUTHENTICATED:
            raise RuntimeError("DIC adapter did not verify an authenticated session")
        return {
            "authentication": "LIVE_AUTHENTICATED",
            "configuration": "VALID",
            "live_contacted": True,
            "mode": "live",
            "session": "AUTHENTICATED",
            "tenant_binding": "VERIFIED_BY_ADAPTER",
            "tenant_configured": True,
        }
    finally:
        await runtime.close()


def _settings(
    *,
    mock: bool = False,
    data_dir: Path | None = None,
    report_error: bool = True,
) -> AppSettings:
    if not mock:
        try:
            return AppSettings()
        except Exception as exc:
            if not report_error:
                raise
            _fail(
                "Configurazione non valida; confronta .env con .env.example "
                f"({type(exc).__name__})."
            )
    overrides: dict[str, Any] = {
        "app_env": "mock",
        "mock_mode": True,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "model_store": False,
        "openai_store": False,
        "model_provider": "openai",
        "openai_model": None,
        "llama_model": None,
        "dic_username": None,
        "dic_expected_tenant_id": None,
        "enable_write_actions": False,
        "enable_live_write_tests": False,
        "discord_application_id": 10_000_003,
        "discord_guild_id": 10_000_001,
        "discord_channel_id": 10_000_002,
        "discord_hr_read_role_ids": (10_000_004,),
        "discord_balance_role_ids": (10_000_004,),
    }
    overrides.update(
        dict.fromkeys(
            (
                "audit_hmac_key",
                "encryption_key",
                "discord_bot_token",
                "openai_api_key",
                "groq_api_key",
                "llama_api_key",
                "dic_password",
                "dic_totp_secret",
                "dic_session_encryption_key",
            )
        )
    )
    overrides.update({field: False for field in AppSettings.WRITE_FLAG_FIELDS})
    if data_dir is not None:
        resolved = data_dir.resolve()
        overrides.update(
            {
                "data_dir": resolved,
                "database_url": "sqlite+aiosqlite:///:memory:",
                "log_dir": resolved / "log",
                "dic_session_state_path": resolved / "session" / "dic_session.enc",
                "pid_file": resolved / "run" / "bh-dic.pid",
                "lock_file": resolved / "run" / "bh-dic.lock",
            }
        )
    return AppSettings(
        **overrides,
        _env_file=None,
        _env_prefix="BH_DIC_ISOLATED_MOCK_",
    )


def _provider_endpoint(settings: AppSettings) -> SplitResult:
    if settings.model_provider == "openai":
        return urlsplit(OPENAI_RESPONSES_BASE_URL)
    if settings.model_provider == "groq":
        return urlsplit(GROQ_OPENAI_BASE_URL)
    return urlsplit(settings.llama_base_url)


def _model_check_offline(settings: AppSettings) -> dict[str, object]:
    endpoint_scope = "official_cloud"
    if settings.model_provider == "llama":
        endpoint_scope = (
            "loopback" if llama_endpoint_is_loopback(settings.llama_base_url) else "remote_https"
        )
    return {
        "status": "UNVERIFIED_OFFLINE",
        "live_contacted": False,
        "provider": settings.model_provider,
        "model": settings.selected_model,
        "endpoint_scope": endpoint_scope,
        "store": False,
        "tool_execution": False,
    }


async def _model_check_live(settings: AppSettings) -> dict[str, object]:
    """Verify authentication, model availability, and one closed synthetic tool call."""

    client = build_intent_client(
        settings,
        developer_prompt=build_intent_router_prompt(settings.language_profile),
    )
    router = OpenAIIntentRouter(client)
    try:
        routed = await router.route(
            "Verifica sintetica del router senza dati personali e senza azioni operative.",
            frozenset(),
        )
    finally:
        await router.close()
    if (
        routed.envelope.function_id != "UNSUPPORTED"
        or routed.metadata.tool_name != "unsupported_request"
    ):
        raise RuntimeError("provider did not honor the closed synthetic tool contract")
    return {
        "status": "LIVE_VERIFIED",
        "live_contacted": True,
        "provider": routed.metadata.provider,
        "model": routed.metadata.model,
        "tool": routed.metadata.tool_name,
        "store": False,
        "tool_execution": False,
    }


async def _with_database[T](
    settings: AppSettings, operation: Callable[[Database], Awaitable[T]]
) -> T:
    database = Database(settings.database_url)
    try:
        return await operation(database)
    finally:
        await database.dispose()


async def _mock_smoke(settings: AppSettings) -> dict[str, object]:
    runtime = await build_runtime(settings)
    try:
        await runtime.database.create_schema()
        await runtime.bot.setup_hook()
        actor = DiscordActor(
            user_id=10_000_005,
            guild_id=10_000_001,
            channel_id=10_000_002,
            logical_roles=frozenset({LogicalRole.HR_READ.value}),
            discord_role_ids=frozenset({10_000_004}),
            entitlements=frozenset({"balances:read"}),
        )
        result = await runtime.coordinator.ask(actor, "Quanti dipendenti attivi ci sono?")
        if not result.success or result.sensitivity.value != "PUBLIC_AGGREGATE":
            raise RuntimeError("mock vertical slice returned an unexpected result")
        commands = runtime.bot.tree.get_commands(guild=runtime.bot.allowed_guild)
        if not any(command.name == "bh" for command in commands):
            raise RuntimeError("guild-scoped command group was not registered locally")
        return {
            "status": "ok",
            "adapter": "mock",
            "discord_gateway_started": False,
            "command_group": "bh",
            "result": result.description,
            "writes_enabled": False,
        }
    finally:
        await runtime.close()


@app.command("version")
def version_command() -> None:
    """Print the package version."""

    typer.echo(__version__)


@app.command("validate-config")
def validate_config(mock: bool = typer.Option(False, help="Validate safe mock defaults.")) -> None:
    """Validate configuration without contacting external services."""

    settings = _settings(mock=mock)
    _emit(settings.safe_summary())


@app.command("model-check")
def model_check(
    live: bool = typer.Option(
        False,
        "--live",
        help="Contact only the selected model provider with one synthetic closed tool call.",
    ),
) -> None:
    """Inspect provider readiness; network and billing are opt-in with ``--live``."""

    settings = _settings()
    if not live:
        _emit(_model_check_offline(settings))
        return
    if settings.mock_mode:
        _fail("--live non e disponibile con MOCK_MODE=true.", code=2)
    try:
        result = _run(_model_check_live(settings))
    except Exception as exc:
        _fail(f"Verifica provider fallita ({type(exc).__name__}).")
    _emit(result)


@app.command("init-db")
def init_db(mock: bool = typer.Option(False, help="Create an isolated mock schema.")) -> None:
    """Apply migrations (or create an in-memory schema in mock mode)."""

    settings = _settings(mock=mock)
    if mock:

        async def create() -> None:
            database = Database(settings.database_url)
            try:
                await database.create_schema()
            finally:
                await database.dispose()

        _run(create())
    else:
        _run(run_migrations_async(settings.database_url))
    _emit({"database": "initialized", "mock": mock})


@app.command("smoke-mock")
def smoke_mock() -> None:
    """Exercise Discord commands, policy, router and adapter without network access."""

    with tempfile.TemporaryDirectory(prefix="bh-dic-smoke-") as temporary:
        settings = _settings(mock=True, data_dir=Path(temporary))
        try:
            result = _run(_mock_smoke(settings))
        except Exception as exc:
            _fail(f"Mock smoke fallito ({type(exc).__name__}).")
    _emit(result)


async def _run_gateway(settings: AppSettings) -> None:
    await run_migrations_async(settings.database_url)
    runtime = await build_runtime(settings)
    token = settings.discord_bot_token
    if token is None:
        await runtime.close()
        raise ValueError("DISCORD_BOT_TOKEN is required")
    try:
        await runtime.bot.start(token.get_secret_value(), reconnect=True)
    finally:
        await runtime.close()


@app.command("run")
def run_command(
    mock: bool = typer.Option(False, help="Use synthetic OpenAI and DIC adapters."),
    check_only: bool = typer.Option(
        False, help="Build and test the mock vertical slice without opening Discord."
    ),
) -> None:
    """Run the Discord gateway in the foreground."""

    if check_only:
        if not mock:
            _fail("--check-only richiede --mock per evitare accessi esterni.", code=2)
        smoke_mock()
        return
    settings = _settings(mock=mock)
    configure_logging(
        log_dir=settings.log_dir.resolve(),
        level=settings.log_level,
        timezone=settings.app_timezone,
    )
    try:
        _run(_run_gateway(settings))
    except KeyboardInterrupt:
        typer.echo("Arresto richiesto.")
    except DicAuthenticationError as exc:
        _dic_auth_failure(exc, code=DIC_AUTH_OUTCOME_UNKNOWN_EXIT_CODE)
    except Exception as exc:
        _fail(f"Avvio rifiutato ({type(exc).__name__}); consulta i log redatti.")


async def _register(settings: AppSettings) -> int:
    await run_migrations_async(settings.database_url)
    runtime = await build_runtime(settings, force_mock_components=True)
    token = settings.discord_bot_token
    if token is None:
        await runtime.close()
        raise ValueError("DISCORD_BOT_TOKEN is required")
    try:
        await runtime.bot.login(token.get_secret_value())
        commands = await runtime.bot.tree.sync(guild=runtime.bot.allowed_guild)
        return len(commands)
    finally:
        await runtime.close()


@app.command("register-commands")
def register_commands() -> None:
    """Register commands only in the configured guild; does not start the bot."""

    settings = _settings()
    try:
        count = _run(_register(settings))
    except Exception as exc:
        _fail(f"Registrazione comandi fallita ({type(exc).__name__}).")
    _emit({"guild_id": settings.discord_guild_id, "registered_commands": count})


@app.command("health")
def health_command(mock: bool = typer.Option(False, help="Use mock configuration.")) -> None:
    """Check configuration and local database only."""

    settings = _settings(mock=mock)

    async def check(database: Database) -> object:
        if mock:
            await database.create_schema()
        return (await HealthChecker(settings, database).check()).model_dump(mode="json")

    try:
        report = _run(_with_database(settings, check))
    except Exception as exc:
        _fail(f"Health check fallito ({type(exc).__name__}).")
    _emit(report)


@app.command("doctor")
def doctor_command(
    mock: bool = typer.Option(False, help="Validate an isolated mock setup."),
    online: bool = typer.Option(False, help="Also resolve external service hostnames."),
) -> None:
    """Run non-secret local diagnostics; network checks are opt-in."""

    settings = _settings(mock=mock)
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 12),
        "configuration": settings.safe_summary(),
        "data_parent_exists": settings.data_dir.resolve().parent.exists(),
        "online_checks_requested": online,
    }
    if online:
        provider_endpoint = _provider_endpoint(settings)
        provider_host = provider_endpoint.hostname
        if provider_host is None:
            _fail("Endpoint provider non valido.")
        provider_port = provider_endpoint.port or (
            443 if provider_endpoint.scheme == "https" else 80
        )
        targets = (
            ("discord.com", 443),
            (provider_host, provider_port),
            ("secure.dipendentincloud.it", 443),
        )
        dns: dict[str, bool] = {}
        for host, port in dict.fromkeys(targets):
            try:
                socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except OSError:
                dns[host] = False
            else:
                dns[host] = True
        checks["dns"] = dns
    _emit(checks)
    if not checks["python_supported"]:
        raise typer.Exit(1)


@app.command("audit-verify")
def audit_verify() -> None:
    """Verify the complete HMAC audit chain."""

    settings = _settings()
    key = settings.audit_hmac_key
    if key is None:
        _fail("AUDIT_HMAC_KEY non configurata.")

    async def verify(database: Database) -> object:
        result = await AuditService(database, key.get_secret_value()).verify()
        if not result.valid:
            raise RuntimeError("audit chain verification failed")
        return result.model_dump(mode="json")

    try:
        result = _run(_with_database(settings, verify))
    except Exception as exc:
        _fail(f"Verifica audit fallita ({type(exc).__name__}).")
    _emit(result)


@app.command("invalidate-session")
def invalidate_session() -> None:
    """Delete only the configured encrypted DIC session vault."""

    settings = _settings()
    key = settings.dic_session_encryption_key
    if key is None:
        _fail("DIC_SESSION_ENCRYPTION_KEY non configurata.")
    session_path = resolve_session_vault_path(settings.dic_session_state_path, settings.data_dir)
    vault = FernetSessionVault(session_path, key.get_secret_value())
    existed = vault.exists()
    vault.invalidate()
    _emit({"encrypted_session_invalidated": existed})


@app.command("dic-auth-check")
def dic_auth_check(
    live: bool = typer.Option(
        False,
        "--live",
        help="Explicitly start the DIC browser adapter and verify the tenant-bound session.",
    ),
) -> None:
    """Verify local DIC auth readiness; network/browser access requires --live."""

    try:
        settings = _settings(report_error=False)
        result = _run(_dic_auth_check_live(settings)) if live else _dic_auth_check_offline(settings)
    except Exception as exc:
        _dic_auth_failure(exc)
    _emit(result)


async def _file_repository[T](
    settings: AppSettings,
    operation: Callable[[SqlAlchemyUploadRepository, Database], Awaitable[T]],
) -> T:
    database = Database(settings.database_url)
    repository = SqlAlchemyUploadRepository(database.sessions)
    try:
        return await operation(repository, database)
    finally:
        await database.dispose()


@files_app.command("list")
def files_list() -> None:
    """List metadata records; never prints document content or original names."""

    settings = _settings()

    async def operation(repository: SqlAlchemyUploadRepository, _database: Database) -> object:
        return [
            {
                "upload_id": item.upload_id,
                "status": item.status.value,
                "size_bytes": item.size_bytes,
                "detected_mime": item.detected_mime,
                "created_at": item.created_at,
                "expires_at": item.expires_at,
            }
            for item in await repository.list_records()
        ]

    _emit(_run(_file_repository(settings, operation)))


@files_app.command("metadata")
def files_metadata(
    upload_id: str = typer.Argument(..., help="Opaque 32-character upload ID."),
) -> None:
    """Print one metadata record without its file content or original filename."""

    settings = _settings()

    async def operation(repository: SqlAlchemyUploadRepository, _database: Database) -> object:
        record = await repository.get(upload_id)
        if record is None:
            raise KeyError(upload_id)
        return {
            "upload_id": record.upload_id,
            "status": record.status.value,
            "bucket": record.bucket,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "claimed_mime": record.claimed_mime,
            "detected_mime": record.detected_mime,
            "antivirus_status": record.antivirus_status,
            "rejection_reason": record.rejection_reason,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "deleted_at": record.deleted_at,
        }

    try:
        result = _run(_file_repository(settings, operation))
    except Exception as exc:
        _fail(f"Metadata non disponibile ({type(exc).__name__}).")
    _emit(result)


@files_app.command("scan")
def files_scan(upload_id: str = typer.Argument(..., help="Opaque 32-character upload ID.")) -> None:
    """Rescan one stored file with configured ClamAV; does not change its status."""

    settings = _settings()

    async def operation(repository: SqlAlchemyUploadRepository, _database: Database) -> object:
        record = await repository.get(upload_id)
        if record is None or record.bucket is None:
            raise KeyError(upload_id)
        path = QuarantineStore((settings.data_dir / "uploads").resolve()).path_for(
            record.bucket, record.upload_id
        )
        result = await asyncio.to_thread(ClamAVScanner(settings.clamav_socket).scan, path)
        return {"upload_id": upload_id, "antivirus_verdict": result.verdict.value}

    try:
        result = _run(_file_repository(settings, operation))
    except Exception as exc:
        _fail(f"Scansione non completata ({type(exc).__name__}).")
    _emit(result)


@files_app.command("purge-expired")
def files_purge_expired() -> None:
    """Delete expired artifacts and retain only deletion metadata."""

    settings = _settings()

    async def operation(repository: SqlAlchemyUploadRepository, _database: Database) -> object:
        service = FileRetentionService(
            store=QuarantineStore((settings.data_dir / "uploads").resolve()),
            repository=repository,
        )
        return {"purged_upload_ids": await service.purge_expired()}

    _emit(_run(_file_repository(settings, operation)))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
