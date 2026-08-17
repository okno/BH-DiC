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
_REDUCTION_NOTICE = "Contenuto ridotto per i limiti Discord."


def _redacted(value: str) -> str:
    return (
        redact_text(value).replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    )


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value or "—"
    if maximum <= 1:
        return "…"
    return f"{value[: maximum - 1]}…"


def _bounded(value: str, maximum: int) -> tuple[str, bool]:
    redacted = _redacted(value)
    return _truncate(redacted, maximum), len(redacted) > maximum


def _safe(value: str, maximum: int) -> str:
    return _bounded(value, maximum)[0]


def _fair_allocations(desired: list[int], budget: int) -> list[int]:
    """Distribute a character budget without starving later result fields."""

    allocations = [0] * len(desired)
    active = [index for index, length in enumerate(desired) if length > 0]
    while budget > 0 and active:
        share = max(1, budget // len(active))
        for index in active:
            granted = min(desired[index] - allocations[index], share, budget)
            allocations[index] += granted
            budget -= granted
            if budget == 0:
                break
        active = [index for index in active if allocations[index] < desired[index]]
    return allocations


def _add_allocations(
    allocations: list[int], desired: list[int], budget: int
) -> tuple[list[int], int]:
    deficits = [maximum - current for current, maximum in zip(allocations, desired, strict=True)]
    additions = _fair_allocations(deficits, budget)
    for index, addition in enumerate(additions):
        allocations[index] += addition
        budget -= addition
    return allocations, budget


def _footer(correlation_id: str | None, *, reduced: bool) -> tuple[str | None, bool]:
    parts: list[str] = []
    correlation_reduced = False
    if reduced:
        parts.append(_REDUCTION_NOTICE)
    if correlation_id:
        safe_correlation, correlation_reduced = _bounded(correlation_id, 128)
        parts.append(f"Correlation ID: {safe_correlation}")
    return (" \N{BULLET} ".join(parts) or None), correlation_reduced


def _build_embed(
    *,
    title: str,
    description: str,
    fields: list[tuple[str, str, bool]],
    footer: str | None,
    color: discord.Color,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    return embed


def _compact_embed(
    *,
    title: str,
    description: str,
    fields: list[tuple[str, str, bool]],
    footer: str,
    color: discord.Color,
    success: bool,
) -> discord.Embed:
    """Fit deterministic data into Discord's aggregate embed character limit."""

    available = _MAX_TOTAL - len(title) - len(footer)
    field_component_lengths = [
        length for name, value, _inline in fields for length in (len(name), len(value))
    ]

    if not fields:
        return _build_embed(
            title=title,
            description=_truncate(description, available),
            fields=[],
            footer=footer,
            color=color,
        )

    # Every field remains visible. Successful data responses reserve useful space
    # for identifiers and values; errors retain their diagnostic description first.
    minimum_field_budget = len(field_component_lengths)
    if success:
        description_length = min(
            len(description),
            1_024,
            max(0, available - minimum_field_budget),
        )
    else:
        description_length = min(
            len(description),
            max(0, available - minimum_field_budget),
        )
    available -= description_length

    allocations = [1] * len(field_component_lengths)
    available -= len(allocations)
    initial_targets = [
        min(length, 128 if index % 2 == 0 else 64)
        for index, length in enumerate(field_component_lengths)
    ]
    allocations, available = _add_allocations(allocations, initial_targets, available)

    if success and available > 0:
        combined = [description_length, *allocations]
        desired = [len(description), *field_component_lengths]
        combined, available = _add_allocations(combined, desired, available)
        description_length, allocations = combined[0], combined[1:]
    elif available > 0:
        allocations, available = _add_allocations(allocations, field_component_lengths, available)

    compact_fields: list[tuple[str, str, bool]] = []
    allocation_index = 0
    for name, value, inline in fields:
        compact_fields.append(
            (
                _truncate(name, allocations[allocation_index]),
                _truncate(value, allocations[allocation_index + 1]),
                inline,
            )
        )
        allocation_index += 2

    compact = _build_embed(
        title=title,
        description=_truncate(description, description_length),
        fields=compact_fields,
        footer=footer,
        color=color,
    )
    if len(compact) > _MAX_TOTAL:  # Defensive guard against library accounting changes.
        compact.description = _truncate(
            compact.description or "—", description_length - (len(compact) - _MAX_TOTAL)
        )
    return compact


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
    title, title_reduced = _bounded(result.title, _MAX_TITLE)
    description, description_reduced = _bounded(result.description, _MAX_DESCRIPTION)
    fields: list[tuple[str, str, bool]] = []
    fields_reduced = len(result.fields) > 25
    for field in result.fields[:25]:
        name, name_reduced = _bounded(field.name, _MAX_FIELD_NAME)
        value, value_reduced = _bounded(field.value, _MAX_FIELD_VALUE)
        fields_reduced = fields_reduced or name_reduced or value_reduced
        fields.append((name, value, field.inline))

    reduced = title_reduced or description_reduced or fields_reduced
    footer, correlation_reduced = _footer(result.correlation_id, reduced=reduced)
    reduced = reduced or correlation_reduced
    if correlation_reduced:
        footer, _ = _footer(result.correlation_id, reduced=True)
    embed = _build_embed(
        title=title,
        description=description,
        fields=fields,
        footer=footer,
        color=color,
    )

    if len(embed) > _MAX_TOTAL:
        reduced = True
        footer, _ = _footer(result.correlation_id, reduced=True)
        if footer is None:  # Defensive fallback; ``reduced=True`` always supplies the notice.
            footer = _REDUCTION_NOTICE
        embed = _compact_embed(
            title=title,
            description=description,
            fields=fields,
            footer=footer,
            color=color,
            success=result.success,
        )

    if profile is not None and not reduced:
        decorated = _decorated_copy(embed, result, profile)
        if len(decorated) <= _MAX_TOTAL:
            return decorated
    return embed
