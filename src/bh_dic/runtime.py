"""Composition root for the single-node BH-DiC runtime."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta

from bh_dic.application import ApplicationError, ApplicationScope, BHApplicationCoordinator
from bh_dic.approvals.confirmation import ConfirmationHasher
from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.approvals.service import ApprovalService
from bh_dic.approvals.storage import ApprovalRepository, SqlAlchemyApprovalRepository
from bh_dic.audit.service import AuditService
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database
from bh_dic.dic.auth import DicSessionManager
from bh_dic.dic.browser import AsyncChromiumSession, BrowserLaunchOptions
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import DicCredentials
from bh_dic.dic.playwright_adapter import PlaywrightDicAdapter
from bh_dic.dic.protocol import DipendentiInCloudAdapter
from bh_dic.dic.session_vault import FernetSessionVault, resolve_session_vault_path
from bh_dic.discord.approvals import PendingViewRecord, PendingViewSource
from bh_dic.discord.bot import BHDiCBot
from bh_dic.discord.checks import DiscordActor, DiscordGate
from bh_dic.files.antivirus import ClamAVScanner
from bh_dic.files.mime import ContentMimeDetector
from bh_dic.files.quarantine import QuarantineStore
from bh_dic.files.repository import SqlAlchemyUploadRepository
from bh_dic.files.service import FileService
from bh_dic.openai.client import ResponsesIntentClient
from bh_dic.openai.intent_router import IntentRouter, MockIntentRouter, OpenAIIntentRouter
from bh_dic.policies.engine import PolicyEngine
from bh_dic.policies.feature_flags import DEFAULT_FEATURE_FLAGS, RuntimeFeatureFlags
from bh_dic.policies.roles import LogicalRole
from bh_dic.security.cipher import PayloadCipher
from bh_dic.services.browser_runtime import (
    BrowserCoordinator,
    BrowserOperationQueue,
    ReadRetryPolicy,
)
from bh_dic.services.dic_service import DicService

_MOCK_SECRET = b"BH-DiC synthetic mock key only!!"


class RepositoryPendingViewSource(PendingViewSource):
    def __init__(self, repository: ApprovalRepository) -> None:
        self._repository = repository

    async def pending_views(self) -> tuple[PendingViewRecord, ...]:
        active = {
            ActionStatus.PENDING,
            ActionStatus.PARTIALLY_APPROVED,
            ActionStatus.APPROVED,
        }
        return tuple(
            PendingViewRecord(action.action_id)
            for action in await self._repository.list_actions()
            if action.status in active
        )


@dataclass(slots=True)
class ApplicationRuntime:
    settings: AppSettings
    database: Database
    adapter: DipendentiInCloudAdapter
    coordinator: BHApplicationCoordinator
    bot: BHDiCBot
    approval_repository: ApprovalRepository
    upload_repository: SqlAlchemyUploadRepository
    browser_session: AsyncChromiumSession | None = None
    session_manager: DicSessionManager | None = None

    async def close(self) -> None:
        if not self.bot.is_closed():
            await self.bot.close()
        await self.adapter.close()
        if self.browser_session is not None:
            await self.browser_session.close()
        await self.database.dispose()


def _secret_bytes(settings: AppSettings, name: str, value: str | None) -> bytes:
    if value:
        return value.encode("utf-8")
    if settings.mock_mode:
        return hashlib.sha256(_MOCK_SECRET + name.encode("ascii")).digest()
    raise ValueError(f"{name} is required")


def _feature_flags(settings: AppSettings) -> RuntimeFeatureFlags:
    baseline = {name: bool(getattr(settings, name.lower())) for name in DEFAULT_FEATURE_FLAGS}
    return RuntimeFeatureFlags(baseline)


def _configured_id(value: int | None, *, mock_value: int, name: str, mock: bool) -> int:
    if value is not None:
        return value
    if mock:
        return mock_value
    raise ValueError(f"{name} is required")


def _discord_gate(settings: AppSettings) -> DiscordGate:
    guild_id = _configured_id(
        settings.discord_guild_id,
        mock_value=10_000_001,
        name="DISCORD_GUILD_ID",
        mock=settings.mock_mode,
    )
    channel_id = _configured_id(
        settings.discord_channel_id,
        mock_value=10_000_002,
        name="DISCORD_CHANNEL_ID",
        mock=settings.mock_mode,
    )
    return DiscordGate(
        guild_id=guild_id,
        channel_id=channel_id,
        allow_dms=settings.discord_allow_dms,
        role_mapping={
            LogicalRole.READ_ONLY.value: settings.discord_readonly_role_ids,
            LogicalRole.HR_READ.value: settings.discord_hr_read_role_ids,
            LogicalRole.HR_WRITE.value: settings.discord_hr_write_role_ids,
            LogicalRole.IAM_OPERATOR.value: settings.discord_iam_role_ids,
            LogicalRole.DOCUMENT_OPERATOR.value: settings.discord_document_role_ids,
            LogicalRole.APPROVER.value: settings.discord_approver_role_ids,
            LogicalRole.SECURITY_ADMIN.value: settings.discord_security_admin_role_ids,
            LogicalRole.SYSTEM_ADMIN.value: settings.discord_system_admin_role_ids,
        },
        entitlement_mapping={"balances:read": settings.discord_balance_role_ids},
    )


async def _adapter(
    settings: AppSettings,
    *,
    force_mock_components: bool,
) -> tuple[DipendentiInCloudAdapter, AsyncChromiumSession | None, DicSessionManager | None]:
    if settings.mock_mode or force_mock_components:
        mock_adapter = MockDicAdapter()
        await mock_adapter.ensure_authenticated()
        return mock_adapter, None, None

    session_path = resolve_session_vault_path(settings.dic_session_state_path, settings.data_dir)
    session_key = settings.dic_session_encryption_key
    if session_key is None:
        raise ValueError("DIC_SESSION_ENCRYPTION_KEY is required")
    session_secret = session_key.get_secret_value()
    vault = FernetSessionVault(session_path, session_secret)
    session_manager = DicSessionManager(vault)
    browser_session = AsyncChromiumSession(
        BrowserLaunchOptions(
            headless=settings.dic_headless,
            locale=settings.dic_locale,
            timezone_id=settings.dic_timezone,
            navigation_timeout_ms=settings.dic_navigation_timeout_seconds * 1_000,
        )
    )
    try:
        page = await browser_session.start(session_manager.load_storage_state())
        browser_coordinator = BrowserCoordinator(
            queue=BrowserOperationQueue(workers=settings.dic_max_concurrent_browser_operations),
            read_retry=ReadRetryPolicy(
                operation_timeout_seconds=settings.dic_navigation_timeout_seconds
            ),
        )
        live_adapter = PlaywrightDicAdapter(
            page,
            base_url=settings.dic_base_url,
            coordinator=browser_coordinator,
            expected_tenant_id=settings.dic_expected_tenant_id,
            quarantine_root=(settings.data_dir / "uploads").resolve(),
            live_writes_enabled=settings.enable_write_actions,
        )
        username = settings.dic_username
        password = settings.dic_password
        if username is None or password is None:
            raise ValueError("DIC credentials are required")
        await live_adapter.ensure_authenticated(
            DicCredentials(
                username=username,
                password=password,
                totp=settings.dic_totp_secret,
            )
        )
        await session_manager.persist(browser_session)
        return live_adapter, browser_session, session_manager
    except Exception:
        await browser_session.close()
        raise


def _router(settings: AppSettings, *, force_mock_components: bool) -> IntentRouter:
    if settings.mock_mode or force_mock_components:
        return MockIntentRouter()
    if settings.openai_api_key is None or settings.openai_model is None:
        raise ValueError("OpenAI configuration is required")
    return OpenAIIntentRouter(
        ResponsesIntentClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            max_output_tokens=settings.openai_max_output_tokens,
            reasoning_effort=settings.openai_reasoning_effort,
        )
    )


async def build_runtime(
    settings: AppSettings,
    *,
    force_mock_components: bool = False,
) -> ApplicationRuntime:
    """Build all boundaries without starting the Discord gateway."""

    database = Database(settings.database_url)
    database.ensure_sqlite_parent()
    flags = _feature_flags(settings)
    audit_material = _secret_bytes(
        settings,
        "AUDIT_HMAC_KEY",
        settings.audit_hmac_key.get_secret_value() if settings.audit_hmac_key else None,
    )
    encryption_material = _secret_bytes(
        settings,
        "ENCRYPTION_KEY",
        settings.encryption_key.get_secret_value() if settings.encryption_key else None,
    )
    approval_repository = SqlAlchemyApprovalRepository(database.sessions)
    upload_repository = SqlAlchemyUploadRepository(database.sessions)
    approval_service = ApprovalService(
        approval_repository,
        ConfirmationHasher(
            hmac.new(audit_material, b"bh-dic:confirmation:v1", hashlib.sha256).digest()
        ),
        writes_enabled=lambda: flags.enabled("ENABLE_WRITE_ACTIONS"),
        default_ttl=timedelta(minutes=settings.pending_action_ttl_minutes),
    )
    capabilities = frozenset({"clamav"}) if settings.clamav_socket else frozenset()
    adapter, browser_session, session_manager = await _adapter(
        settings, force_mock_components=force_mock_components
    )
    dic_service = DicService(adapter, flags, capabilities=capabilities)
    file_store = QuarantineStore((settings.data_dir / "uploads").resolve())
    file_service = FileService(
        store=file_store,
        repository=upload_repository,
        mime_detector=ContentMimeDetector(),
        antivirus=ClamAVScanner(settings.clamav_socket),
        max_bytes=settings.upload_max_mb * 1024 * 1024,
        retention=timedelta(hours=settings.upload_retention_hours),
        allowed_mime_types=frozenset(settings.upload_allowed_mime_types),
        clamav_required=settings.clamav_required,
    )
    gate = _discord_gate(settings)
    bot_holder: dict[str, BHDiCBot] = {}

    async def resolve_requester(action: PendingAction) -> DiscordActor:
        bot = bot_holder.get("bot")
        if bot is None:
            raise ApplicationError("Discord requester resolver is unavailable")
        guild = bot.get_guild(int(action.guild_id))
        if guild is None:
            raise ApplicationError("requester guild is unavailable")
        member = guild.get_member(int(action.requester_id))
        if member is None:
            member = await guild.fetch_member(int(action.requester_id))
        return gate.authorize(
            user_id=member.id,
            guild_id=int(action.guild_id),
            channel_id=int(action.channel_id),
            role_ids=[role.id for role in member.roles],
            is_thread=False,
            is_bot=member.bot,
            is_webhook=False,
        )

    coordinator = BHApplicationCoordinator(
        router=_router(settings, force_mock_components=force_mock_components),
        policy=PolicyEngine(),
        flags=flags,
        dic=dic_service,
        scope=ApplicationScope(
            allowed_guild_ids=frozenset({str(gate.guild_id)}),
            allowed_channel_ids=frozenset({str(gate.channel_id)}),
            current_tenant_id=settings.dic_expected_tenant_id or "TENANT-SYNTHETIC-MOCK",
            allowed_tenant_ids=frozenset(
                {settings.dic_expected_tenant_id or "TENANT-SYNTHETIC-MOCK"}
            ),
            capabilities=capabilities,
        ),
        audit=AuditService(database, audit_material),
        approvals=approval_service,
        approval_repository=approval_repository,
        payload_cipher=PayloadCipher(encryption_material),
        files=file_service,
        pseudonym_key=hmac.new(audit_material, b"bh-dic:pseudonym:v1", hashlib.sha256).digest(),
        requester_actor_resolver=(
            None if settings.mock_mode or force_mock_components else resolve_requester
        ),
    )
    application_id = _configured_id(
        settings.discord_application_id,
        mock_value=10_000_003,
        name="DISCORD_APPLICATION_ID",
        mock=settings.mock_mode,
    )
    bot = BHDiCBot(
        application_id=application_id,
        guild_id=gate.guild_id,
        gate=gate,
        coordinator=coordinator,
        interaction_mode=settings.discord_interaction_mode,
        upload_max_bytes=settings.upload_max_mb * 1024 * 1024,
        pending_view_source=RepositoryPendingViewSource(approval_repository),
    )
    bot_holder["bot"] = bot
    return ApplicationRuntime(
        settings=settings,
        database=database,
        adapter=adapter,
        coordinator=coordinator,
        bot=bot,
        approval_repository=approval_repository,
        upload_repository=upload_repository,
        browser_session=browser_session,
        session_manager=session_manager,
    )
