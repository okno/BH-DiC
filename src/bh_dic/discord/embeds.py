"""Deterministic and redacted Discord response rendering."""

from __future__ import annotations

import discord

from bh_dic.discord.interactions import InteractionResult
from bh_dic.openai.redaction import redact_text

_MAX_TITLE = 256
_MAX_DESCRIPTION = 4_096
_MAX_FIELD_NAME = 256
_MAX_FIELD_VALUE = 1_024


def _safe(value: str, maximum: int) -> str:
    redacted = (
        redact_text(value).replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    )
    return redacted[:maximum] or "—"


def result_embed(result: InteractionResult) -> discord.Embed:
    color = discord.Color.green() if result.success else discord.Color.red()
    embed = discord.Embed(
        title=_safe(result.title, _MAX_TITLE),
        description=_safe(result.description, _MAX_DESCRIPTION),
        color=color,
    )
    for field in result.fields[:25]:
        embed.add_field(
            name=_safe(field.name, _MAX_FIELD_NAME),
            value=_safe(field.value, _MAX_FIELD_VALUE),
            inline=field.inline,
        )
    if result.correlation_id:
        embed.set_footer(text=f"Correlation ID: {_safe(result.correlation_id, 128)}")
    return embed
