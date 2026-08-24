from __future__ import annotations

import pytest

from bh_dic.discord.checks import AccessDenialReason, DiscordAccessDenied, DiscordGate
from bh_dic.discord.interactions import InteractionResult, ResponseSensitivity


@pytest.fixture
def gate() -> DiscordGate:
    return DiscordGate(
        guild_id=100,
        channel_id=200,
        role_mapping={"HR_READ": {300}, "HR_WRITE": {301}},
    )


def test_gate_authorizes_only_exact_guild_channel_and_mapped_role(gate: DiscordGate) -> None:
    actor = gate.authorize(user_id=1, guild_id=100, channel_id=200, role_ids={300})
    assert actor.logical_roles == frozenset({"HR_READ"})


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (
            {"guild_id": None, "channel_id": None, "role_ids": {300}},
            AccessDenialReason.DM_NOT_ALLOWED,
        ),
        (
            {"guild_id": 999, "channel_id": 200, "role_ids": {300}},
            AccessDenialReason.GUILD_NOT_ALLOWED,
        ),
        (
            {"guild_id": 100, "channel_id": 999, "role_ids": {300}},
            AccessDenialReason.CHANNEL_NOT_ALLOWED,
        ),
        (
            {"guild_id": 100, "channel_id": 200, "role_ids": {999}},
            AccessDenialReason.ROLE_NOT_ALLOWED,
        ),
        (
            {"guild_id": 100, "channel_id": 200, "role_ids": {300}, "is_thread": True},
            AccessDenialReason.THREAD_NOT_ALLOWED,
        ),
    ],
)
def test_gate_denies_invalid_context(
    gate: DiscordGate, kwargs: dict[str, object], reason: AccessDenialReason
) -> None:
    with pytest.raises(DiscordAccessDenied) as caught:
        gate.authorize(user_id=1, **kwargs)  # type: ignore[arg-type]
    assert caught.value.reason == reason


def test_sensitive_result_is_ephemeral() -> None:
    assert InteractionResult(title="x", description="y").ephemeral is True
    public = InteractionResult(
        title="x", description="y", sensitivity=ResponseSensitivity.PUBLIC_AGGREGATE
    )
    assert public.ephemeral is False


def test_dm_authorization_requires_explicit_role_and_verified_guild() -> None:
    gate = DiscordGate(
        guild_id=100,
        channel_id=200,
        role_mapping={"HR_READ": {300}},
        allow_dms=True,
        dm_allowed_role_ids={300},
    )
    actor = gate.authorize_dm(user_id=1, verified_guild_id=100, role_ids={300})
    assert actor.guild_id == 100
    assert actor.channel_id == 200
    assert actor.logical_roles == frozenset({"HR_READ"})

    with pytest.raises(DiscordAccessDenied) as denied:
        gate.authorize_dm(user_id=1, verified_guild_id=100, role_ids={999})
    assert denied.value.reason is AccessDenialReason.ROLE_NOT_ALLOWED
    with pytest.raises(DiscordAccessDenied) as foreign:
        gate.authorize_dm(user_id=1, verified_guild_id=999, role_ids={300})
    assert foreign.value.reason is AccessDenialReason.GUILD_NOT_ALLOWED


def test_dm_mode_cannot_be_enabled_without_an_explicit_role_allowlist() -> None:
    with pytest.raises(ValueError, match="explicit allowed-role"):
        DiscordGate(
            guild_id=100,
            channel_id=200,
            role_mapping={"HR_READ": {300}},
            allow_dms=True,
        )
