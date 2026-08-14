"""Normalization and validation for untrusted Discord/site/file metadata."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath


class InputValidationError(ValueError):
    pass


_EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CONTROL_WHITESPACE = {"\t", "\n", "\r"}


def normalize_text(
    value: str,
    *,
    max_length: int,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InputValidationError("value must be text")
    normalized = unicodedata.normalize("NFKC", value)
    output: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("C"):
            if allow_newlines and character in _CONTROL_WHITESPACE:
                output.append("\n" if character in {"\n", "\r"} else " ")
            continue
        output.append(character)
    cleaned = "".join(output).strip()
    if len(cleaned) > max_length:
        raise InputValidationError(f"text exceeds {max_length} characters")
    return cleaned


def validate_employee_id(value: str) -> str:
    normalized = normalize_text(value, max_length=64)
    if not _EMPLOYEE_ID.fullmatch(normalized):
        raise InputValidationError("invalid employee ID")
    return normalized


def contains_path_traversal(value: str) -> bool:
    if not isinstance(value, str) or "\x00" in value:
        return True
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return True
    if "/" in normalized or "\\" in normalized:
        return True
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    return (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive != ""
        or any(part in {"", ".", ".."} for part in posix.parts)
        or any(part in {"", ".", ".."} for part in windows.parts)
    )


def sanitize_filename_metadata(value: str) -> str:
    if contains_path_traversal(value):
        raise InputValidationError("unsafe attachment filename")
    normalized = normalize_text(value, max_length=255)
    if contains_path_traversal(normalized):
        raise InputValidationError("unsafe attachment filename")
    if normalized in {".", ".."}:
        raise InputValidationError("unsafe attachment filename")
    return normalized


def sanitize_discord_text(value: str, *, max_length: int = 1_500) -> str:
    """Normalize output and prevent mentions from application-generated text."""

    cleaned = normalize_text(value, max_length=max_length, allow_newlines=True)
    cleaned = re.sub(
        r"@(everyone|here)",
        lambda match: f"@\u200b{match.group(1)}",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("<@", "<\u200b@").replace("<#", "<\u200b#")
    return cleaned
