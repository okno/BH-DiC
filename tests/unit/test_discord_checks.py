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
