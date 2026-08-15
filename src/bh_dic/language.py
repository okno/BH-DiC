"""Closed, local-only language profile for safe presentation customization."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Language = Literal["it", "en"]
Tone = Literal["professional", "friendly", "concise", "empathetic"]
AddressStyle = Literal["tu", "lei", "neutral"]
Verbosity = Literal["concise", "standard", "detailed"]
EmojiMode = Literal["off", "status"]

_MAX_DISPLAY_NAME = 48
_MAX_DECORATION = 120

_MENTION = re.compile(r"@|<[@#][!&]?\d+>")
_URL = re.compile(
    r"(?i)(?:\b(?:https?|ftp|file|javascript|data|mailto):|\bwww\.|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\[[^\]]+\]\([^)]*\))"
)
_TOKEN_LIKE = re.compile(
    r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\b(?:github_pat_|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{8,}|"
    r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[A-Za-z0-9_-]{40,}\b|"
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=]))"
)
_PROMPT_INJECTION = re.compile(
    r"(?i)(?:"
    r"\b(?:ignore|disregard|forget|override)\b.{0,48}"
    r"\b(?:instructions?|prompt|polic(?:y|ies)|system|developer|tools?)\b|"
    r"\b(?:ignora|dimentica|sovrascrivi|aggira)\b.{0,48}"
    r"\b(?:istruzion\w*|prompt|regol\w*|policy|sistema|sviluppatore|tools?)\b|"
    r"\b(?:jailbreak|system prompt|developer message|bypass authorization)\b"
    r")"
)


def _safe_decoration(value: str, *, field_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in normalized):
        raise ValueError(f"{field_name} contains control characters")
    candidate = normalized.strip()
    if not candidate:
        raise ValueError(f"{field_name} must not be blank")
    if _MENTION.search(candidate):
        raise ValueError(f"{field_name} must not contain mentions")
    if _URL.search(candidate):
        raise ValueError(f"{field_name} must not contain URLs")
    if _TOKEN_LIKE.search(candidate):
        raise ValueError(f"{field_name} contains token-like material")
    if _PROMPT_INJECTION.search(candidate):
        raise ValueError(f"{field_name} contains instruction-like material")
    return candidate


class BotLanguageProfile(BaseModel):
    """Immutable style choices; never an authorization or free-form instruction channel.

    ``language`` controls only decorative/local phrasing and an optional provider clarification
    question. It does not translate deterministic HR data, errors, audit records, or identifiers.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    language: Language = "it"
    tone: Tone = "professional"
    address_style: AddressStyle = "neutral"
    verbosity: Verbosity = "standard"
    emoji_mode: EmojiMode = "off"
    display_name: str | None = Field(default=None, max_length=_MAX_DISPLAY_NAME)
    opening: str | None = Field(default=None, max_length=_MAX_DECORATION)
    closing: str | None = Field(default=None, max_length=_MAX_DECORATION)

    @field_validator("display_name", "opening", "closing", mode="before")
    @classmethod
    def validate_decorative_text(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name or "decoration"
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
        if not value.strip(" ") and field_name in {"display_name", "opening", "closing"}:
            return None
        return _safe_decoration(value, field_name=field_name)


DEFAULT_LANGUAGE_PROFILE = BotLanguageProfile()

__all__ = [
    "DEFAULT_LANGUAGE_PROFILE",
    "AddressStyle",
    "BotLanguageProfile",
    "EmojiMode",
    "Language",
    "Tone",
    "Verbosity",
]
