"""Transport-level guild, channel, DM, and role allowlists."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class AccessDenialReason(StrEnum):
    DM_NOT_ALLOWED = "DM_NOT_ALLOWED"
    GUILD_NOT_ALLOWED = "GUILD_NOT_ALLOWED"
    CHANNEL_NOT_ALLOWED = "CHANNEL_NOT_ALLOWED"
    THREAD_NOT_ALLOWED = "THREAD_NOT_ALLOWED"
    BOT_NOT_ALLOWED = "BOT_NOT_ALLOWED"
    WEBHOOK_NOT_ALLOWED = "WEBHOOK_NOT_ALLOWED"
    ROLE_NOT_ALLOWED = "ROLE_NOT_ALLOWED"


class DiscordAccessDenied(PermissionError):
    def __init__(self, reason: AccessDenialReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DiscordActor:
    user_id: int
    guild_id: int
    channel_id: int
    logical_roles: frozenset[str]
    discord_role_ids: frozenset[int]
    entitlements: frozenset[str] = frozenset()


class DiscordGate:
    """A deny-by-default gate independent from discord.py objects."""

    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        role_mapping: Mapping[str, Iterable[int]],
        entitlement_mapping: Mapping[str, Iterable[int]] | None = None,
        allow_dms: bool = False,
    ) -> None:
        if guild_id <= 0 or channel_id <= 0:
            raise ValueError("guild_id and channel_id must be positive")
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.allow_dms = allow_dms
        self.role_mapping = {
            logical_role: frozenset(int(role_id) for role_id in role_ids)
            for logical_role, role_ids in role_mapping.items()
        }
        self.entitlement_mapping = {
            entitlement: frozenset(int(role_id) for role_id in role_ids)
            for entitlement, role_ids in (entitlement_mapping or {}).items()
        }

    def authorize(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        channel_id: int | None,
        role_ids: Iterable[int],
        is_thread: bool = False,
        is_bot: bool = False,
        is_webhook: bool = False,
    ) -> DiscordActor:
        if is_bot:
            raise DiscordAccessDenied(AccessDenialReason.BOT_NOT_ALLOWED)
        if is_webhook:
            raise DiscordAccessDenied(AccessDenialReason.WEBHOOK_NOT_ALLOWED)
        if guild_id is None or channel_id is None:
            if not self.allow_dms:
                raise DiscordAccessDenied(AccessDenialReason.DM_NOT_ALLOWED)
            # HR operations are never authorized from a DM even when a future
            # informational DM mode is enabled.
            raise DiscordAccessDenied(AccessDenialReason.DM_NOT_ALLOWED)
        if guild_id != self.guild_id:
            raise DiscordAccessDenied(AccessDenialReason.GUILD_NOT_ALLOWED)
        if is_thread:
            raise DiscordAccessDenied(AccessDenialReason.THREAD_NOT_ALLOWED)
        if channel_id != self.channel_id:
            raise DiscordAccessDenied(AccessDenialReason.CHANNEL_NOT_ALLOWED)

        actual_role_ids = frozenset(int(role_id) for role_id in role_ids)
        logical_roles = frozenset(
            logical_role
            for logical_role, configured_ids in self.role_mapping.items()
            if configured_ids & actual_role_ids
        )
        if not logical_roles:
            raise DiscordAccessDenied(AccessDenialReason.ROLE_NOT_ALLOWED)
        entitlements = frozenset(
            entitlement
            for entitlement, configured_ids in self.entitlement_mapping.items()
            if configured_ids & actual_role_ids
        )
        return DiscordActor(
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            logical_roles=logical_roles,
            discord_role_ids=actual_role_ids,
            entitlements=entitlements,
        )
