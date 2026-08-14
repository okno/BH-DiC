"""Best-effort secret removal before structured logs or user-visible errors."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bh_dic.security.pii import redact_pii, redact_pii_text

_TOKEN_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret|cookie)\s*[:=]\s*)[^\s,;]+"),
)


class SecretRedactor:
    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._known = tuple(
            sorted(
                {secret for secret in known_secrets if secret and len(secret) >= 4},
                key=len,
                reverse=True,
            )
        )

    def redact_text(self, value: str, *, include_pii: bool = True) -> str:
        output = value
        for secret in self._known:
            output = output.replace(secret, "[SECRET_REDACTED]")
        for pattern in _TOKEN_PATTERNS:
            output = pattern.sub(r"\1[SECRET_REDACTED]", output)
        return redact_pii_text(output) if include_pii else output

    def redact(self, value: Any, *, include_pii: bool = True) -> Any:
        if isinstance(value, str):
            return self.redact_text(value, include_pii=include_pii)
        if include_pii:
            value = redact_pii(value)
        if isinstance(value, dict):
            return {key: self.redact(item, include_pii=include_pii) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.redact(item, include_pii=include_pii) for item in value]
        return value


def has_private_file_permissions(path: str | Path) -> bool:
    """Check 0600-style secrecy on POSIX; ACL validation is platform-specific on Windows."""

    target = Path(path)
    if os.name == "nt":
        return target.is_file()
    mode = stat.S_IMODE(target.stat().st_mode)
    return mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
