"""Generation and one-way verification of short-lived confirmation codes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from dataclasses import dataclass

_ALPHABET = string.ascii_uppercase.replace("I", "").replace("O", "") + "23456789"


@dataclass(frozen=True, slots=True)
class ConfirmationMaterial:
    code: str
    salt: bytes
    digest: bytes


class ConfirmationHasher:
    def __init__(self, key: bytes, *, code_length: int = 10) -> None:
        if len(key) < 32:
            raise ValueError("confirmation HMAC key must contain at least 32 bytes")
        if code_length < 8:
            raise ValueError("confirmation code must contain at least 8 characters")
        self._key = bytes(key)
        self._code_length = code_length

    def create(self, action_id: str) -> ConfirmationMaterial:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(self._code_length))
        salt = secrets.token_bytes(16)
        return ConfirmationMaterial(code, salt, self._digest(action_id, salt, code))

    def verify(self, action_id: str, salt: bytes, expected: bytes, candidate: str) -> bool:
        normalized = "".join(candidate.strip().upper().split())
        actual = self._digest(action_id, salt, normalized)
        return hmac.compare_digest(actual, expected)

    def _digest(self, action_id: str, salt: bytes, code: str) -> bytes:
        payload = action_id.encode("utf-8") + b"\x00" + salt + b"\x00" + code.encode("ascii")
        return hmac.new(self._key, payload, hashlib.sha256).digest()
