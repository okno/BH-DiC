from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
from bh_dic.dic.auth import DicAuthOutcomeUnknownError, DicAuthStage
from bh_dic.dic.browser import AsyncChromiumSession
from bh_dic.dic.errors import DicSessionVaultError
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import SessionState, SessionStatus
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
        _env_file=None,
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
        dic_expected_tenant_id="123456789",
        _env_file=None,
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


def test_router_uses_closed_language_profile_without_sending_decorations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_client(_settings: AppSettings, *, developer_prompt: str) -> object:
        captured["prompt"] = developer_prompt
        return object()

    monkeypatch.setattr(runtime_module, "build_intent_client", fake_client)
    settings = _live_settings().model_copy(
        update={
            "bot_tone": "friendly",
            "bot_address_style": "lei",
            "bot_opening": "Buongiorno dal team HR",
        }
    )

    assert isinstance(
        runtime_module._router(settings, force_mock_components=False), OpenAIIntentRouter
    )
    assert "tono cordiale" in captured["prompt"]
    assert "forma di cortesia" in captured["prompt"]
    assert "Buongiorno dal team HR" not in captured["prompt"]
    assert "PRIORITA ASSOLUTA" in captured["prompt"]


@pytest.mark.asyncio
async def test_adapter_mock_and_live_composition_never_start_real_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, browser, manager = await runtime_module._adapter(
        _mock_settings(),
        force_mock_components=False,
        state_digest_key=b"s" * 32,
        authenticate_dic=False,
    )
    assert isinstance(adapter, MockDicAdapter)
    assert browser is None and manager is None
    await adapter.close()

    events: list[str] = []
    adapter_options: dict[str, object] = {}
    restored_session_storage = object()

    class FakeVault:
        def __init__(self, path: object, key: object) -> None:
            events.append(f"vault:{bool(path)}:{bool(key)}")

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> object:
            events.append("load")
            return SimpleNamespace(
                storage_state={"cookies": []},
                session_storage=restored_session_storage,
            )

        async def persist(self, _session: object) -> None:
            events.append("persist")

    class FakeBrowser:
        closed = False

        def __init__(self, _options: object) -> None:
            pass

        async def start(self, state: object, *, session_storage: object = None) -> object:
            assert session_storage is restored_session_storage
            events.append(f"start:{bool(state)}")
            return object()

        async def close(self) -> None:
            self.closed = True
            events.append("browser-close")

    class FakeLiveAdapter:
        def __init__(self, _page: object, **kwargs: object) -> None:
            adapter_options.update(kwargs)

        async def ensure_authenticated(self, credentials: object) -> SessionStatus:
            events.append(f"authenticate:{bool(credentials)}")
            return SessionStatus(state=SessionState.AUTHENTICATED)

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr(runtime_module, "FernetSessionVault", FakeVault)
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    live_adapter, live_browser, live_manager = await runtime_module._adapter(
        _live_settings(),
        force_mock_components=False,
        state_digest_key=b"s" * 32,
        authenticate_dic=False,
    )
    assert type(cast(Any, live_adapter)) is FakeLiveAdapter
    assert isinstance(live_browser, FakeBrowser)
    assert isinstance(live_manager, FakeSessionManager)
    assert adapter_options["login_timeout_ms"] == 60_000
    assert events[-2:] == ["load", "start:True"]
    assert not any(event.startswith("authenticate:") for event in events)
    assert "persist" not in events

    passive_persist = cast(
        Callable[[], Awaitable[None]], adapter_options["verified_session_callback"]
    )
    await passive_persist()
    assert events[-1] == "persist"

    await runtime_module._adapter(
        _live_settings(),
        force_mock_components=False,
        state_digest_key=b"s" * 32,
        authenticate_dic=True,
    )
    assert events[-2:] == ["authenticate:True", "persist"]

    missing_key = _live_settings().model_copy(update={"dic_session_encryption_key": None})
    with pytest.raises(ValueError, match="DIC_SESSION_ENCRYPTION_KEY"):
        await runtime_module._adapter(
            missing_key,
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=False,
        )

    missing_credentials = _live_settings().model_copy(
        update={"dic_username": None, "dic_password": None}
    )
    passive_adapter, passive_browser, passive_manager = await runtime_module._adapter(
        missing_credentials,
        force_mock_components=False,
        state_digest_key=b"s" * 32,
        authenticate_dic=False,
    )
    assert type(cast(Any, passive_adapter)) is FakeLiveAdapter
    assert isinstance(passive_browser, FakeBrowser)
    assert isinstance(passive_manager, FakeSessionManager)
    with pytest.raises(ValueError, match="DIC credentials"):
        await runtime_module._adapter(
            missing_credentials,
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )
    assert events[-1] == "browser-close"


@pytest.mark.asyncio
async def test_passive_verified_session_persistence_is_serialized_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_options: dict[str, object] = {}
    active_persists = 0
    maximum_active_persists = 0
    completed_persists = 0
    sensitive = "private-passive-session-persistence-marker"
    session_managers: list[Any] = []

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            self.fail = False
            session_managers.append(self)

        def load_session(self) -> None:
            return None

        async def persist(self, _session: object) -> None:
            nonlocal active_persists, maximum_active_persists, completed_persists
            if self.fail:
                raise RuntimeError(sensitive)
            active_persists += 1
            maximum_active_persists = max(maximum_active_persists, active_persists)
            await asyncio.sleep(0)
            active_persists -= 1
            completed_persists += 1

    class FakeBrowser:
        def __init__(self, _options: object) -> None:
            pass

        async def start(self, _state: object, *, session_storage: object = None) -> object:
            assert session_storage is None
            return object()

        async def close(self) -> None:
            return None

    class FakeLiveAdapter:
        def __init__(self, _page: object, **kwargs: object) -> None:
            adapter_options.update(kwargs)

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    await runtime_module._adapter(
        _live_settings(),
        force_mock_components=False,
        state_digest_key=b"s" * 32,
        authenticate_dic=False,
    )
    callback = cast(Callable[[], Awaitable[None]], adapter_options["verified_session_callback"])

    await asyncio.gather(*(callback() for _ in range(5)))

    assert completed_persists == 5
    assert maximum_active_persists == 1

    manager = session_managers[0]
    manager.fail = True
    with pytest.raises(DicSessionVaultError, match="verified DIC session") as caught:
        await callback()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)


@pytest.mark.asyncio
async def test_unverified_live_authentication_is_never_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            return None

        async def persist(self, _session: object) -> None:
            events.append("persist")

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

        async def ensure_authenticated(self, _credentials: object) -> SessionStatus:
            events.append("authenticate-unverified")
            return SessionStatus(state=SessionState.UNKNOWN)

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await runtime_module._adapter(
            _live_settings(),
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert events == ["authenticate-unverified", "browser-close"]


@pytest.mark.asyncio
async def test_invalid_session_vault_degrades_passive_gateway_but_blocks_explicit_auth(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            raise DicSessionVaultError("PRIVATE-VAULT-DETAIL")

    class FakeBrowser:
        def __init__(self, _options: object) -> None:
            pass

        async def start(self, state: object, *, session_storage: object = None) -> object:
            assert state is None
            assert session_storage is None
            events.append("browser-start")
            return object()

        async def close(self) -> None:
            events.append("browser-close")

    class FakeLiveAdapter:
        def __init__(self, _page: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    with caplog.at_level("WARNING", logger="bh_dic.runtime"):
        adapter, browser, manager = await runtime_module._adapter(
            _live_settings(),
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=False,
        )

    assert type(cast(Any, adapter)) is FakeLiveAdapter
    assert isinstance(browser, FakeBrowser)
    assert isinstance(manager, FakeSessionManager)
    assert events == ["browser-start"]
    assert any(record.message == "dic_session_restore_unavailable" for record in caplog.records)
    assert "PRIVATE-VAULT-DETAIL" not in caplog.text

    with pytest.raises(DicSessionVaultError, match="PRIVATE-VAULT-DETAIL"):
        await runtime_module._adapter(
            _live_settings(),
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )
    assert events[-1] == "browser-close"


@pytest.mark.asyncio
async def test_authenticated_session_persistence_failure_is_nonrepeatable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive = "private-session-persistence-marker"
    events: list[str] = []

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            return None

        async def persist(self, _session: object) -> None:
            events.append("persist")
            raise RuntimeError(sensitive)

    class FakeBrowser:
        def __init__(self, _options: object) -> None:
            pass

        async def start(self, _state: object, *, session_storage: object = None) -> object:
            assert session_storage is None
            return object()

        async def close(self) -> None:
            events.append("browser-close")
            raise RuntimeError("private-browser-close-marker")

    class FakeLiveAdapter:
        def __init__(self, _page: object, **_kwargs: object) -> None:
            pass

        async def ensure_authenticated(self, _credentials: object) -> SessionStatus:
            events.append("authenticate")
            return SessionStatus(state=SessionState.AUTHENTICATED)

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await runtime_module._adapter(
            _live_settings(),
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value)
    assert "private-browser-close-marker" not in str(caught.value)
    assert events == ["authenticate", "persist", "browser-close"]


@pytest.mark.asyncio
async def test_cancelled_session_persistence_is_nonrepeatable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    persist_started = asyncio.Event()

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            return None

        async def persist(self, _session: object) -> None:
            events.append("persist")
            persist_started.set()
            await asyncio.Event().wait()

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

        async def ensure_authenticated(self, _credentials: object) -> SessionStatus:
            events.append("authenticate")
            return SessionStatus(state=SessionState.AUTHENTICATED)

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    operation = asyncio.create_task(
        asyncio.wait_for(
            runtime_module._adapter(
                _live_settings(),
                force_mock_components=False,
                state_digest_key=b"s" * 32,
                authenticate_dic=True,
            ),
            timeout=0.05,
        )
    )
    await asyncio.wait_for(persist_started.wait(), timeout=1)

    with pytest.raises(DicAuthOutcomeUnknownError) as caught:
        await operation

    assert caught.value.stage is DicAuthStage.CREDENTIAL_SUBMIT
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert events == ["authenticate", "persist", "browser-close"]


@pytest.mark.asyncio
async def test_pre_submit_cancellation_still_closes_browser_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    auth_started = asyncio.Event()

    class FakeSessionManager:
        def __init__(self, _vault: object) -> None:
            pass

        def load_session(self) -> None:
            return None

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
            auth_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(runtime_module, "FernetSessionVault", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "DicSessionManager", FakeSessionManager)
    monkeypatch.setattr(runtime_module, "AsyncChromiumSession", FakeBrowser)
    monkeypatch.setattr(runtime_module, "PlaywrightDicAdapter", FakeLiveAdapter)

    operation = asyncio.create_task(
        runtime_module._adapter(
            _live_settings(),
            force_mock_components=False,
            state_digest_key=b"s" * 32,
            authenticate_dic=True,
        )
    )
    await asyncio.wait_for(auth_started.wait(), timeout=1)
    operation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert events == ["browser-close"]


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
        _settings: AppSettings,
        *,
        force_mock_components: bool,
        state_digest_key: bytes,
        authenticate_dic: bool,
    ) -> tuple[DipendentiInCloudAdapter, None, None]:
        assert force_mock_components is False
        assert authenticate_dic is False
        assert len(state_digest_key) == 32
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
