from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

from bh_dic.discord.bot import BHDiCBot
from bh_dic.discord.checks import DiscordGate
from bh_dic.discord.commands import BHCommandGroup
from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import (
    InteractionCoordinator,
    InteractionResult,
    ResponseSensitivity,
    ResultField,
)
from bh_dic.discord.views import (
    ApprovalCodeModal,
    ApprovalView,
    EmployeeSelect,
    EmployeeSelectView,
    PaginationView,
    RejectReasonModal,
)
from bh_dic.language import BotLanguageProfile
from bh_dic.security.rate_limit import SlidingWindowRateLimiter

ACTION_ID = "12345678-1234-4234-9234-123456789abc"


@dataclass(slots=True)
class FakeRole:
    id: int


@dataclass(slots=True)
class FakeUser:
    id: int = 10
    bot: bool = False
    roles: tuple[FakeRole, ...] = (FakeRole(30),)
    mention: str = "<@10>"


class FakeResponse:
    def __init__(self, *, done: bool = False) -> None:
        self.done = done
        self.sent: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.deferred: list[dict[str, object]] = []
        self.modals: list[discord.ui.Modal] = []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, *args: object, **kwargs: object) -> None:
        self.sent.append((args, kwargs))
        self.done = True

    async def defer(self, **kwargs: object) -> None:
        self.deferred.append(kwargs)
        self.done = True

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.modals.append(modal)
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def send(self, *args: object, **kwargs: object) -> None:
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self, *, done: bool = False, role_id: int = 30) -> None:
        self.user = FakeUser(roles=(FakeRole(role_id),))
        self.guild_id = 10
        self.channel_id = 20
        self.channel = SimpleNamespace(id=20)
        self.response = FakeResponse(done=done)
        self.followup = FakeFollowup()


class FakeMessage:
    def __init__(
        self,
        *,
        content: str = "conteggio",
        channel_id: int = 20,
        author: FakeUser | None = None,
    ) -> None:
        self.author = author or FakeUser()
        self.webhook_id: int | None = None
        self.guild: SimpleNamespace | None = SimpleNamespace(id=10)
        self.channel: object = SimpleNamespace(id=channel_id)
        self.content = content
        self.mentions: list[FakeUser] = []
        self.replies: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def reply(self, *args: object, **kwargs: object) -> None:
        self.replies.append((args, kwargs))


class FakeAttachment:
    def __init__(self, content: bytes, *, size: int | None = None) -> None:
        self.filename = "synthetic.pdf"
        self.content_type = "application/pdf"
        self.size = len(content) if size is None else size
        self._content = content
        self.read_calls: list[bool] = []

    async def read(self, *, use_cached: bool = False) -> bytes:
        self.read_calls.append(use_cached)
        return self._content


def _gate() -> DiscordGate:
    return DiscordGate(
        guild_id=10,
        channel_id=20,
        role_mapping={"HR_READ": {30}},
    )


def test_everyone_role_can_grant_only_the_configured_logical_role_in_the_allowed_channel() -> None:
    guild_id = 10
    gate = DiscordGate(
        guild_id=guild_id,
        channel_id=20,
        role_mapping={"READ_ONLY": {guild_id}, "HR_READ": {30}},
    )

    actor = gate.authorize(
        user_id=40,
        guild_id=guild_id,
        channel_id=20,
        role_ids=[guild_id],
    )

    assert actor.logical_roles == frozenset({"READ_ONLY"})
    assert actor.discord_role_ids == frozenset({guild_id})


def _coordinator(
    result: InteractionResult | None = None,
) -> tuple[InteractionCoordinator, SimpleNamespace]:
    response = result or InteractionResult(title="Titolo", description="Risposta")
    raw = SimpleNamespace(
        ask=AsyncMock(return_value=response),
        help=AsyncMock(return_value=response),
        status=AsyncMock(return_value=response),
        health=AsyncMock(return_value=response),
        pending=AsyncMock(return_value=response),
        approve=AsyncMock(return_value=response),
        reject=AsyncMock(return_value=response),
        upload=AsyncMock(return_value=response),
        employee=AsyncMock(return_value=response),
        contracts=AsyncMock(return_value=response),
        documents=AsyncMock(return_value=response),
        balances=AsyncMock(return_value=response),
        prepare_operator_action=AsyncMock(return_value=response),
    )
    return cast(InteractionCoordinator, raw), raw


def _interaction(
    *, done: bool = False, role_id: int = 30
) -> tuple[discord.Interaction, FakeInteraction]:
    raw = FakeInteraction(done=done, role_id=role_id)
    return cast(discord.Interaction, raw), raw


def test_embed_is_deterministic_bounded_and_redacted() -> None:
    result = InteractionResult(
        title="T" * 300 + " @everyone",
        description="Bearer top-secret-token @here" + "D" * 5_000,
        fields=tuple(ResultField(f"name-{index}", "value") for index in range(30)),
        correlation_id="corr-safe-123",
        success=False,
    )
    embed = result_embed(result)

    assert embed.color == discord.Color.red()
    assert embed.title is not None and len(embed.title) == 256
    assert embed.description is not None and len(embed.description) == 4_096
    assert "top-secret-token" not in embed.description
    assert "@\u200bhere" in embed.description
    assert len(embed.fields) == 25
    assert embed.footer.text == "Correlation ID: corr-safe-123"

    empty = result_embed(InteractionResult(title="", description="", success=True))
    assert empty.color == discord.Color.green()
    assert empty.title
    assert empty.description


@pytest.mark.asyncio
async def test_approval_modals_and_persistent_view_dispatch_callbacks() -> None:
    approve = AsyncMock()
    reject = AsyncMock()
    with pytest.raises(ValueError, match="UUID"):
        ApprovalView("invalid", approve, reject)

    view = ApprovalView(ACTION_ID, approve, reject)
    interaction, raw = _interaction()
    await view._approve(interaction)
    await view._reject(interaction)
    assert len(raw.response.modals) == 2
    assert all(item.timeout == 180 for item in raw.response.modals)
    buttons = [cast(discord.ui.Button[ApprovalView], child) for child in view.children]
    assert all(button.custom_id and ACTION_ID in button.custom_id for button in buttons)

    approval_modal = ApprovalCodeModal(ACTION_ID, approve)
    approval_modal.confirmation_code._value = "ABCD"
    approval_modal.target_confirmation._value = " DELETE 42 "
    await approval_modal.on_submit(interaction)
    approve.assert_awaited_once_with(interaction, ACTION_ID, "ABCD", "DELETE 42")

    rejection_modal = RejectReasonModal(ACTION_ID, reject)
    rejection_modal.reason._value = "Motivazione sintetica"
    await rejection_modal.on_submit(interaction)
    reject.assert_awaited_once_with(interaction, ACTION_ID, "Motivazione sintetica")


@pytest.mark.asyncio
async def test_selection_and_pagination_views_dispatch_without_network() -> None:
    selection = AsyncMock()
    choices = [(str(index), f"Employee {index}" + "x" * 120) for index in range(30)]
    select = EmployeeSelect(choices, selection)
    assert len(select.options) == 25
    assert all(len(option.label) <= 100 and len(option.value) <= 100 for option in select.options)
    select._values = ["7"]
    interaction, raw = _interaction()
    await select.callback(interaction)
    selection.assert_awaited_once_with(interaction, "7")
    assert len(EmployeeSelectView(choices, selection).children) == 1

    previous = AsyncMock()
    following = AsyncMock()
    pagination = PaginationView(previous, following)
    first = cast(discord.ui.Button[PaginationView], pagination.children[0])
    second = cast(discord.ui.Button[PaginationView], pagination.children[1])
    await first.callback(interaction)
    raw.response.done = False
    await second.callback(interaction)
    assert len(raw.response.deferred) == 2
    previous.assert_awaited_once_with()
    following.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_command_send_success_action_denial_rate_limit_and_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator, _raw_coordinator = _coordinator()
    group = BHCommandGroup(gate=_gate(), coordinator=coordinator)
    interaction, raw = _interaction()
    operation = AsyncMock(
        return_value=InteractionResult(
            title="Pending",
            description="Needs approval",
            action_id=ACTION_ID,
        )
    )
    await group._send(interaction, operation)
    assert raw.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert isinstance(raw.followup.sent[0][1]["view"], ApprovalView)

    denied, denied_raw = _interaction(role_id=999)
    await group._send(denied, operation)
    assert denied_raw.response.sent[0][1]["ephemeral"] is True

    denied_done, denied_done_raw = _interaction(done=True, role_id=999)
    await group._send(denied_done, operation)
    assert denied_done_raw.followup.sent[0][1]["ephemeral"] is True

    limited_group = BHCommandGroup(
        gate=_gate(),
        coordinator=coordinator,
        rate_limiter=SlidingWindowRateLimiter(limit=1, window_seconds=60),
    )
    first, _first_raw = _interaction()
    await limited_group._send(first, AsyncMock(return_value=InteractionResult("ok", "ok")))
    second, second_raw = _interaction()
    await limited_group._send(second, operation)
    assert "Troppe richieste" in cast(str, second_raw.response.sent[0][0][0])

    second_done, second_done_raw = _interaction(done=True)
    await limited_group._send(second_done, operation)
    assert "Troppe richieste" in cast(str, second_done_raw.followup.sent[0][0][0])

    failing_group = BHCommandGroup(gate=_gate(), coordinator=coordinator)
    failed, failed_raw = _interaction()
    private_detail = "EMP-SYNTH-001 Mario Rossi must stay private"
    await failing_group._send(failed, AsyncMock(side_effect=RuntimeError(private_detail)))
    assert "correlation ID" in cast(str, failed_raw.followup.sent[0][0][0])
    assert private_detail not in caplog.text

    with pytest.raises(ValueError, match="positive"):
        BHCommandGroup(gate=_gate(), coordinator=coordinator, upload_max_bytes=0)


@pytest.mark.asyncio
async def test_command_transport_applies_only_the_validated_local_profile() -> None:
    coordinator, _ = _coordinator(InteractionResult("Titolo", "Risultato", success=True))
    group = BHCommandGroup(
        gate=_gate(),
        coordinator=coordinator,
        language_profile=BotLanguageProfile(
            display_name="Assistente HR",
            opening="Ecco il risultato autorizzato.",
            closing="Operazione conclusa.",
            emoji_mode="status",
        ),
    )
    interaction, raw = _interaction()
    await group._send(interaction, AsyncMock(return_value=InteractionResult("Titolo", "Risultato")))

    embed = cast(discord.Embed, raw.followup.sent[0][1]["embed"])
    assert embed.author.name == "✅ Assistente HR"
    assert embed.description is not None and embed.description.startswith("Ecco il risultato")
    assert embed.footer.text == "Operazione conclusa."


async def _invoke(
    command: Any,
    group: BHCommandGroup,
    interaction: discord.Interaction,
    *args: object,
) -> None:
    callback = cast(Callable[..., Awaitable[None]], command.callback)
    await callback(group, interaction, *args)


@pytest.mark.asyncio
async def test_all_slash_command_routes_and_upload_guards() -> None:
    coordinator, raw_coordinator = _coordinator()
    group = BHCommandGroup(gate=_gate(), coordinator=coordinator, upload_max_bytes=8)

    routes: tuple[tuple[Any, tuple[object, ...]], ...] = (
        (BHCommandGroup.ask, ("domanda",)),
        (BHCommandGroup.help_command, ()),
        (BHCommandGroup.status_command, ()),
        (BHCommandGroup.health_command, ()),
        (BHCommandGroup.pending_command, ()),
        (BHCommandGroup.approve_command, (ACTION_ID, "ABCD", "DELETE 42")),
        (
            BHCommandGroup.reject_command,
            (
                ACTION_ID,
                "Motivazione",
            ),
        ),
        (BHCommandGroup.employee_command, ("EMP-1",)),
        (BHCommandGroup.contracts_command, ("EMP-1", "2026-01-01", "2026-12-31")),
        (BHCommandGroup.documents_command, ("EMP-1", "ready")),
        (BHCommandGroup.balances_command, ("EMP-1", 2026)),
    )
    for command, arguments in routes:
        interaction, _raw = _interaction()
        await _invoke(command, group, interaction, *arguments)

    assert raw_coordinator.ask.await_count == 1
    assert raw_coordinator.help.await_count == 1
    assert raw_coordinator.status.await_count == 1
    assert raw_coordinator.health.await_count == 1
    assert raw_coordinator.pending.await_count == 1
    assert raw_coordinator.approve.await_count == 1
    assert raw_coordinator.reject.await_count == 1
    assert raw_coordinator.employee.await_count == 1
    assert raw_coordinator.contracts.await_count == 1
    assert raw_coordinator.documents.await_count == 1
    assert raw_coordinator.balances.await_count == 1

    invalid_dates, invalid_raw = _interaction()
    await _invoke(BHCommandGroup.contracts_command, group, invalid_dates, None, "not-a-date", None)
    assert "ISO" in cast(str, invalid_raw.response.sent[0][0][0])

    too_large = FakeAttachment(b"x", size=9)
    oversized, oversized_raw = _interaction()
    await _invoke(
        BHCommandGroup.upload_command,
        group,
        oversized,
        "EMP-1",
        "contract",
        cast(discord.Attachment, too_large),
    )
    assert too_large.read_calls == []
    embed = cast(discord.Embed, oversized_raw.followup.sent[0][1]["embed"])
    assert embed.title is not None and "grande" in embed.title

    attachment = FakeAttachment(b"content")
    uploaded, _uploaded_raw = _interaction()
    await _invoke(
        BHCommandGroup.upload_command,
        group,
        uploaded,
        "EMP-1",
        "contract",
        cast(discord.Attachment, attachment),
    )
    assert attachment.read_calls == [True]
    payload = raw_coordinator.upload.await_args.args[-1]
    assert payload.content == b"content"


@pytest.mark.asyncio
async def test_operator_slash_routes_are_explicit_and_catalog_deterministic() -> None:
    coordinator, raw_coordinator = _coordinator(
        InteractionResult("Pending", "Preview", action_id=ACTION_ID)
    )
    group = BHCommandGroup(gate=_gate(), coordinator=coordinator)
    routes: tuple[tuple[Any, tuple[object, ...]], ...] = (
        (
            BHCommandGroup.operator_balance_correction_command,
            ("EMP-1", 2026, 8, "Ferie", "2", "3", "Correzione autorizzata"),
        ),
        (
            BHCommandGroup.operator_rbac_update_command,
            ("EMP-1", "Cambio autorizzato", "Employee", False),
        ),
        (
            BHCommandGroup.operator_document_download_command,
            ("EMP-1", "DOC-1", "Accesso autorizzato"),
        ),
        (
            BHCommandGroup.operator_employee_delete_command,
            ("EMP-1", "Cessazione verificata"),
        ),
        (
            BHCommandGroup.operator_contract_delete_command,
            ("EMP-1", "CON-1", "Contratto errato"),
        ),
    )
    for command, arguments in routes:
        interaction, raw = _interaction()
        await _invoke(command, group, interaction, *arguments)
        assert isinstance(raw.followup.sent[0][1]["view"], ApprovalView)

    routed = [call.args[1:] for call in raw_coordinator.prepare_operator_action.await_args_list]
    assert routed == [
        (
            "EMP-BAL-002",
            "EMP-1",
            {
                "year": 2026,
                "month": 8,
                "category": "Ferie",
                "previous_value": "2",
                "amount": "3",
                "motivation": "Correzione autorizzata",
            },
        ),
        (
            "EMP-RBAC-002",
            "EMP-1",
            {
                "motivation": "Cambio autorizzato",
                "role_name": "Employee",
                "enabled": False,
            },
        ),
        (
            "EMP-DOC-003",
            "EMP-1",
            {"document_id": "DOC-1", "motivation": "Accesso autorizzato"},
        ),
        (
            "EMP-DELETE-001",
            "EMP-1",
            {"motivation": "Cessazione verificata"},
        ),
        (
            "EMP-CONTRACT-003",
            "EMP-1",
            {"contract_id": "CON-1", "motivation": "Contratto errato"},
        ),
    ]
    operator_names = {
        command.name for command in group.commands if command.name.startswith("operator-")
    }
    assert operator_names == {
        "operator-balance-correction",
        "operator-rbac-update",
        "operator-document-download",
        "operator-employee-delete",
        "operator-contract-delete",
    }


@pytest.mark.asyncio
async def test_bot_setup_and_message_modes_are_offline_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator, raw_coordinator = _coordinator(
        InteractionResult(
            "Conteggio",
            "Totale: 1",
            sensitivity=ResponseSensitivity.PUBLIC_AGGREGATE,
        )
    )

    class PendingSource:
        async def pending_views(self) -> tuple[SimpleNamespace, ...]:
            return (SimpleNamespace(action_id=ACTION_ID, message_id=123),)

    bot = BHDiCBot(
        application_id=100,
        guild_id=10,
        gate=_gate(),
        coordinator=coordinator,
        interaction_mode="channel",
        pending_view_source=cast(Any, PendingSource()),
    )
    await bot.setup_hook()
    assert any(command.name == "bh" for command in bot.tree.get_commands(guild=bot.allowed_guild))
    assert bot.intents.message_content
    await bot.on_ready()

    message = FakeMessage()
    await bot.on_message(cast(discord.Message, message))
    assert raw_coordinator.ask.await_count == 1
    assert isinstance(message.replies[0][1]["embed"], discord.Embed)

    sensitive_coordinator, _ = _coordinator(InteractionResult("Sensitive", "Private"))
    sensitive_bot = BHDiCBot(
        application_id=101,
        guild_id=10,
        gate=_gate(),
        coordinator=sensitive_coordinator,
        interaction_mode="channel",
    )
    sensitive_message = FakeMessage()
    await sensitive_bot.on_message(cast(discord.Message, sensitive_message))
    assert "ephemeral" in cast(str, sensitive_message.replies[0][0][0])

    failing_coordinator, failing_raw = _coordinator()
    private_detail = "EMP-SYNTH-001 Mario Rossi must stay private"
    failing_raw.ask.side_effect = RuntimeError(private_detail)
    failing_bot = BHDiCBot(
        application_id=105,
        guild_id=10,
        gate=_gate(),
        coordinator=failing_coordinator,
        interaction_mode="channel",
    )
    failed_message = FakeMessage()
    await failing_bot.on_message(cast(discord.Message, failed_message))
    assert "non completata" in cast(str, failed_message.replies[0][0][0])
    assert private_detail not in caplog.text

    denied = FakeMessage(author=FakeUser(roles=(FakeRole(999),)))
    await bot.on_message(cast(discord.Message, denied))
    assert denied.replies == []

    limited_bot = BHDiCBot(
        application_id=102,
        guild_id=10,
        gate=_gate(),
        coordinator=coordinator,
        interaction_mode="channel",
        rate_limiter=SlidingWindowRateLimiter(limit=1, window_seconds=60),
    )
    await limited_bot.on_message(cast(discord.Message, FakeMessage()))
    limited = FakeMessage()
    await limited_bot.on_message(cast(discord.Message, limited))
    assert limited.replies == []

    slash_bot = BHDiCBot(
        application_id=103,
        guild_id=10,
        gate=_gate(),
        coordinator=coordinator,
        interaction_mode="slash",
    )
    assert not slash_bot.intents.message_content
    ignored = FakeMessage()
    await slash_bot.on_message(cast(discord.Message, ignored))
    assert ignored.replies == []

    mention_bot = BHDiCBot(
        application_id=104,
        guild_id=10,
        gate=_gate(),
        coordinator=coordinator,
        interaction_mode="mention",
    )
    no_mention = FakeMessage()
    await mention_bot.on_message(cast(discord.Message, no_mention))
    assert no_mention.replies == []

    wrong_channel = FakeMessage(channel_id=999)
    await bot.on_message(cast(discord.Message, wrong_channel))
    wrong_channel.guild = None
    await bot.on_message(cast(discord.Message, wrong_channel))
    wrong_channel.guild = SimpleNamespace(id=10)
    wrong_channel.webhook_id = 1
    await bot.on_message(cast(discord.Message, wrong_channel))

    class FakeThread:
        pass

    monkeypatch.setattr("bh_dic.discord.bot.discord.Thread", FakeThread)
    thread_message = FakeMessage()
    thread_message.channel = FakeThread()
    await bot.on_message(cast(discord.Message, thread_message))

    for client in (bot, sensitive_bot, failing_bot, limited_bot, slash_bot, mention_bot):
        await client.close()
