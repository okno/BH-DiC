"""Data minimization before provider calls and deterministic result rendering."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_IBAN = re.compile(r"(?i)\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b")
_ITALIAN_FISCAL_CODE = re.compile(r"(?i)\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?39[ .-]?)?(?:0\d{1,3}|3\d{2})[ .-]?\d(?:[ .-]?\d){5,9}(?!\w)")
_SECRET = re.compile(
    r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|"
    r"\b(?:token|password|secret|cookie)\s*[:=]\s*\S+)"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignora le istruzioni precedenti",
    "system prompt",
    "esegui javascript",
    "run javascript",
    "shell command",
    "bypass policy",
)


class UnsafePromptError(ValueError):
    """The request contains a high-confidence policy-bypass marker."""


def normalize_user_text(value: str, *, max_length: int = 2_000) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _CONTROL.sub("", normalized).strip()
    if not normalized:
        raise ValueError("empty request")
    if len(normalized) > max_length:
        raise ValueError("request is too long")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        raise UnsafePromptError("suspicious prompt-injection marker")
    return normalized


def redact_text(value: str) -> str:
    value = _SECRET.sub("[SECRET_REDACTED]", value)
    value = _IBAN.sub("[IBAN_REDACTED]", value)
    value = _ITALIAN_FISCAL_CODE.sub("[FISCAL_CODE_REDACTED]", value)
    value = _EMAIL.sub("[EMAIL_REDACTED]", value)
    return _PHONE.sub("[PHONE_REDACTED]", value)


def prepare_provider_input(value: str) -> str:
    return redact_text(normalize_user_text(value))


def redact_structure(value: Any) -> Any:
    """Recursively redact JSON-like output without mutating the input."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(key): redact_structure(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_structure(child) for child in value]
    return value
