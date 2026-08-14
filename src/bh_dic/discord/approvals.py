"""Helpers for restoring persistent Discord approval controls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import discord

from bh_dic.discord.commands import BHCommandGroup
from bh_dic.discord.views import ApprovalView


@dataclass(frozen=True, slots=True)
class PendingViewRecord:
    action_id: str
    message_id: int | None = None


class PendingViewSource(Protocol):
    async def pending_views(self) -> Sequence[PendingViewRecord]: ...


async def restore_approval_views(
    bot: discord.Client,
    command_group: BHCommandGroup,
    source: PendingViewSource,
) -> int:
    records = await source.pending_views()
    for record in records:
        view = ApprovalView(
            record.action_id,
            command_group._approve_from_view,
            command_group._reject_from_view,
        )
        bot.add_view(view, message_id=record.message_id)
    return len(records)
