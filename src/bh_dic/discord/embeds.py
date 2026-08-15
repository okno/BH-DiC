"""Deterministic and redacted Discord response rendering."""

from __future__ import annotations

import discord

from bh_dic.discord.interactions import InteractionResult
from bh_dic.language import BotLanguageProfile
from bh_dic.openai.redaction import redact_text

_MAX_TITLE = 256
_MAX_DESCRIPTION = 4_096
_MAX_FIELD_NAME = 256
_MAX_FIELD_VALUE = 1_024
_MAX_AUTHOR_NAME = 256
_MAX_FOOTER = 2_048
_MAX_TOTAL = 6_000


def _safe(value: str, maximum: int) -> str:
    redacted = (
        redact_text(value).replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    )
    return redacted[:maximum] or "—"


def _decorated_copy(
    embed: discord.Embed,
    result: InteractionResult,
    profile: BotLanguageProfile,
) -> discord.Embed:
    decorated = embed.copy()
    if result.success and profile.opening is not None:
        opening = _safe(profile.opening, 120)
        description = decorated.description or ""
        candidate = f"{opening}\n\n{description}"
        if len(candidate) <= _MAX_DESCRIPTION:
            decorated.description = candidate

    status = ""
    if profile.emoji_mode == "status":
        status = "\N{WHITE HEAVY CHECK MARK}" if result.success else "\N{WARNING SIGN}"
    author = " ".join(part for part in (status, profile.display_name) if part)
    if author:
        decorated.set_author(name=_safe(author, _MAX_AUTHOR_NAME))

    footer_parts: list[str] = []
    if result.success and profile.closing is not None:
        footer_parts.append(_safe(profile.closing, 120))
    if result.correlation_id:
        footer_parts.append(f"Correlation ID: {_safe(result.correlation_id, 128)}")
    if footer_parts:
        decorated.set_footer(text=_safe(" \N{BULLET} ".join(footer_parts), _MAX_FOOTER))
    return decorated


def result_embed(
    result: InteractionResult, profile: BotLanguageProfile | None = None
) -> discord.Embed:
    """Render deterministic data; a profile may add bounded local decoration only."""

    color = discord.Color.green() if result.success else discord.Color.red()
    embed = discord.Embed(
        title=_safe(result.title, _MAX_TITLE),
        description=_safe(result.description, _MAX_DESCRIPTION),
        color=color,
    )
    if result.correlation_id:
        embed.set_footer(text=f"Correlation ID: {_safe(result.correlation_id, 128)}")
    for field in result.fields[:25]:
        embed.add_field(
            name=_safe(field.name, _MAX_FIELD_NAME),
            value=_safe(field.value, _MAX_FIELD_VALUE),
            inline=field.inline,
        )

    if profile is not None:
        decorated = _decorated_copy(embed, result, profile)
        if len(decorated) <= _MAX_TOTAL:
            return decorated
    return embed
