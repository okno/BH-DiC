from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import bh_dic.runtime as runtime_module
from bh_dic.application import ApplicationError, BHApplicationCoordinator
from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.approvals.storage import ApprovalRepository
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database
from bh_dic.dic.browser import AsyncChromiumSession
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.protocol import DipendentiInCloudAdapter
from bh_dic.discord.bot import BHDiCBot
from bh_dic.files.repository import SqlAlchemyUploadRepository
from bh_dic.openai.intent_router import MockIntentRouter, OpenAIIntentRouter
from bh_dic.runtime import ApplicationRuntime, RepositoryPendingViewSource


def _mock_settings() -> AppSettings:
    return AppSettings(
        app_env="mock",
        mock_mode=True,
        database_url="sqlite+aiosqlite:///:memory:",
        discord_application_id=103,
        discord_guild_id=101,
        discord_channel_id=102,
        discord_hr_read_role_ids=(201,),
        discord_balance_role_ids=(202,),
    )


def _live_settings() -> AppSettings:
    return AppSettings(
        app_env="production",
        mock_mode=False,
        database_url="sqlite+aiosqlite:///:memory:",
        audit_hmac_key="A" * 32,
        encryption_key="E" * 32,
        discord_bot_token="synthetic-discord-token",
        discord_application_id=103,
        discord_guild_id=101,
        discord_channel_id=102,
        discord_hr_read_role_ids=(201,),
        discord_balance_role_ids=(202,),
        openai_api_key="synthetic-openai-key",
        openai_model="synthetic-model",
        dic_username="synthetic-user",
        dic_password="synthetic-password",
        dic_session_encryption_key="S" * 32,
        dic_expected_tenant_id="TENANT-SYNTHETIC",
    )


class FakePendingRepository:
    def __init__(self) -> None:
        self.actions = (
            SimpleNamespace(action_id="pending", status=ActionStatus.PENDING),
            SimpleNamespace(action_id="partial", status=ActionStatus.PARTIALLY_APPROVED),
            SimpleNamespace(action_id="approved", status=ActionStatus.APPROVED),
            SimpleNamespace(action_id="done", status=ActionStatus.SUCCEEDED),
        )

    async def list_actions(self) -> tuple[Any, ...]:
        return self.actions


@pytest.mark.asyncio
async def test_pending_view_source_selects_only_restart_safe_states() -> None:
    source = RepositoryPendingViewSource(cast(ApprovalRepository, FakePendingRepository()))
    records = await source.pending_views()
    assert tuple(record.action_id for record in records) == ("pending", "partial", "approved")


def test_runtime_security_helpers_are_fail_closed_and_deterministic() -> None:
    mock = _mock_settings()
    first = runtime_module._secret_bytes(mock, "AUDIT_HMAC_KEY", None)
    second = runtime_module._secret_bytes(mock, "AUDIT_HMAC_KEY", None)
    assert first == second
    assert len(first) == 32
    assert runtime_module._secret_bytes(mock, "name", "configured") == b"configured"

    non_mock = AppSettings.model_construct(mock_mode=False)
    with pytest.raises(ValueError, match="required"):
        runtime_module._secret_bytes(non_mock, "MISSING", None)

    assert runtime_module._configured_id(7, mock_value=8, name="ID", mock=False) == 7
    assert runtime_module._configured_id(None, mock_value=8, name="ID", mock=True) == 8
    with pytest.raises(ValueError, match="ID is required"):
        runtime_module._configured_id(None, mock_value=8, name="ID", mock=False)

    flags = runtime_module._feature_flags(mock)
    assert flags.enabled("ENABLE_READ_ACTIONS")
    assert not flags.enabled("ENABLE_WRITE_ACTIONS")

    gate = runtime_module._discord_gate(mock)
    actor = gate.authorize(user_id=1, guild_id=101, channel_id=102, role_ids=(201, 202))
    assert actor.logical_roles == frozenset({"HR_READ"})
    assert actor.entitlements == frozenset({"balances:read"})


def test_router_uses_mock_offline_and_requires_live_openai_configuration() -> None:
    assert isinstance(
        runtime_module._router(_mock_settings(), force_mock_components=False), MockIntentRouter
    )
    assert isinstance(
        runtime_module._router(_live_settings(), force_mock_components=False), OpenAIIntentRouter
    )
    missing = _live_settings().model_copy(update={"openai_api_key": None})
    with pytest.raises(ValueError, match="OpenAI configuration"):
        runtime_module._router(missing, force_mock_components=False)
    assert isinstance(runtime_module._router(missing, force_mock_components=True), MockIntentRouter)


@pytest.mark.asyncio
async def test_adapter_mock_and_live_composition_never_start_real_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, browser, manager = await runtime_module._adapter(
        _mock_settings(), force_mock_components=False
    )
    assert isinstance(adapter, MockDicAdapter)
    assert browser is None and manager is None
    await adapter.close()

    events: list[str] = []

    class FakeVault:
        def __init__(self, path: object, key: object) -> None:
            events.append(f"vault:{bool(path)}:{bool(key)}")

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_storage_state(self) -> dict[str, object] | None:
            events.append("load")
            return {"cookies": []}

        async def persist(self, _session: object) -> None:
            events.append("persist")

    class FakeBrowser:
        closed = False

        def __init__(self, _options: object) -> None:
            pass

        async def start(self, state: object) -> object:
            events.append(f"start:{bool(state)}")
            return object()

        async def close(self) -> None:
            self.closed = True
            events.append("browser-close")

    class FakeLiveAdapter:
        def __init__(self, _page: object, **_kwargs: object) -> None:
            pass

        async def ensure_authenticated(self, credentials: object) -> None:
            events.append(f"authenticate:{bool(credentials)}")

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr(runtime_module, "FernetSessionVault", FakeVault)
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    live_adapter, live_browser, live_manager = await runtime_module._adapter(
        _live_settings(), force_mock_components=False
    )
    assert type(cast(Any, live_adapter)) is FakeLiveAdapter
    assert isinstance(live_browser, FakeBrowser)
    assert isinstance(live_manager, FakeSessionManager)
    assert events[-1] == "persist"

    missing_key = _live_settings().model_copy(update={"dic_session_encryption_key": None})
    with pytest.raises(ValueError, match="DIC_SESSION_ENCRYPTION_KEY"):
        await runtime_module._adapter(missing_key, force_mock_components=False)

    missing_credentials = _live_settings().model_copy(
        update={"dic_username": None, "dic_password": None}
    )
    with pytest.raises(ValueError, match="DIC credentials"):
        await runtime_module._adapter(missing_credentials, force_mock_components=False)
    assert events[-1] == "browser-close"


@pytest.mark.asyncio
async def test_application_runtime_close_releases_every_owned_boundary() -> None:
    bot = SimpleNamespace(is_closed=lambda: False, close=AsyncMock())
    adapter = SimpleNamespace(close=AsyncMock())
    browser = SimpleNamespace(close=AsyncMock())
    database = SimpleNamespace(dispose=AsyncMock())
    runtime = ApplicationRuntime(
        settings=_mock_settings(),
        database=cast(Database, database),
        adapter=cast(DipendentiInCloudAdapter, adapter),
        coordinator=cast(BHApplicationCoordinator, object()),
        bot=cast(BHDiCBot, bot),
        approval_repository=cast(ApprovalRepository, object()),
        upload_repository=cast(SqlAlchemyUploadRepository, object()),
        browser_session=cast(AsyncChromiumSession, browser),
    )
    await runtime.close()
    bot.close.assert_awaited_once_with()
    adapter.close.assert_awaited_once_with()
    browser.close.assert_awaited_once_with()
    database.dispose.assert_awaited_once_with()

    closed_bot = SimpleNamespace(is_closed=lambda: True, close=AsyncMock())
    runtime.bot = cast(BHDiCBot, closed_bot)
    runtime.browser_session = None
    await runtime.close()
    closed_bot.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_runtime_live_requester_resolver_rechecks_member_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_adapter = MockDicAdapter()
    await mock_adapter.ensure_authenticated()

    async def fake_adapter(
        _settings: AppSettings, *, force_mock_components: bool
    ) -> tuple[DipendentiInCloudAdapter, None, None]:
        assert force_mock_components is False
        return mock_adapter, None, None

    monkeypatch.setattr(runtime_module, "_adapter", fake_adapter)
    monkeypatch.setattr(
        runtime_module,
        "_router",
        lambda _settings, *, force_mock_components: MockIntentRouter(),
    )
    runtime = await runtime_module.build_runtime(_live_settings())
    resolver = runtime.coordinator.requester_actor_resolver
    assert resolver is not None
    action = cast(
        PendingAction,
        SimpleNamespace(guild_id="101", channel_id="102", requester_id="501"),
    )

    monkeypatch.setattr(BHDiCBot, "get_guild", lambda _self, _guild_id: None)
    with pytest.raises(ApplicationError, match="guild"):
        await resolver(action)

    member = SimpleNamespace(id=501, roles=(SimpleNamespace(id=201),), bot=False)

    class FakeGuild:
        def __init__(self, cached: object | None) -> None:
            self.cached = cached
            self.fetch_member = AsyncMock(return_value=member)

        def get_member(self, _member_id: int) -> object | None:
            return self.cached

    cached_guild = FakeGuild(member)
    monkeypatch.setattr(BHDiCBot, "get_guild", lambda _self, _guild_id: cached_guild)
    actor = await resolver(action)
    assert actor.user_id == 501
    assert actor.logical_roles == frozenset({"HR_READ"})
    cached_guild.fetch_member.assert_not_awaited()

    uncached_guild = FakeGuild(None)
    monkeypatch.setattr(BHDiCBot, "get_guild", lambda _self, _guild_id: uncached_guild)
    assert (await resolver(action)).user_id == 501
    uncached_guild.fetch_member.assert_awaited_once_with(501)
    await runtime.close()
