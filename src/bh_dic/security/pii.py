"""Central PII redaction and stable pseudonymization."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from typing import Any

_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_ITALIAN_TAX_CODE = re.compile(
    r"(?<![A-Z0-9])[A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z](?![A-Z0-9])",
    re.I,
)
_IBAN = re.compile(r"(?<![A-Z0-9])IT\s*\d{2}(?:\s*[A-Z0-9]){23}(?![A-Z0-9])", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?39[ .-]?)?(?:\d[ .-]?){8,12}(?!\w)")

_SECRET_KEYS = frozenset(
    {
        "password",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "totp_secret",
        "storage_state",
        "confirmation_code",
    }
)
_PII_KEYS = frozenset(
    {
        "iban",
        "codice_fiscale",
        "tax_code",
        "phone",
        "telefono",
        "address",
        "indirizzo",
        "birth_date",
        "data_di_nascita",
        "internal_notes",
        "note_interne",
        "email",
    }
)


def redact_pii_text(value: str) -> str:
    redacted = _EMAIL.sub("[EMAIL_REDACTED]", value)
    redacted = _ITALIAN_TAX_CODE.sub("[TAX_CODE_REDACTED]", redacted)
    redacted = _IBAN.sub("[IBAN_REDACTED]", redacted)
    redacted = _PHONE.sub("[PHONE_REDACTED]", redacted)
    return redacted


def redact_pii(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 8:
        return "[REDACTED_DEPTH_LIMIT]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.strip().lower().replace("-", "_").replace(" ", "_")
            if any(secret in normalized_key for secret in _SECRET_KEYS):
                result[key] = "[SECRET_REDACTED]"
            elif normalized_key in _PII_KEYS:
                result[key] = "[PII_REDACTED]"
            else:
                result[key] = redact_pii(item, _depth=_depth + 1)
        return result
    if isinstance(value, str):
        return redact_pii_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_pii(item, _depth=_depth + 1) for item in value]
    return value


def pseudonymize_identifier(value: str, key: bytes, *, length: int = 16) -> str:
    if len(key) < 32:
        raise ValueError("pseudonymization key must contain at least 32 bytes")
    if not 8 <= length <= 64:
        raise ValueError("pseudonym length must be between 8 and 64")
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"emp_{digest[:length]}"
