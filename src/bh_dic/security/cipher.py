"""Authenticated encryption for small JSON payloads persisted by workflows."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class PayloadDecryptionError(ValueError):
    """Opaque error that never includes ciphertext or decrypted data."""


def _validate_json(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("JSON payload exceeds the maximum nesting depth")
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite numbers are not valid payload values")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json(child, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for child in value:
            _validate_json(child, depth=depth + 1)
        return
    raise ValueError("payload must contain only JSON-compatible values")


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class PayloadCipher:
    """Fernet wrapper with canonical JSON and a checked purpose envelope."""

    def __init__(self, key: str | bytes) -> None:
        material = key.encode("ascii") if isinstance(key, str) else bytes(key)
        if len(material) == 32:
            material = base64.urlsafe_b64encode(material)
        try:
            self._fernet = Fernet(material)
        except (TypeError, ValueError) as exc:
            if len(material) < 32:
                raise ValueError("payload encryption key must contain at least 32 bytes") from exc
            derived = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
            self._fernet = Fernet(derived)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt_json(
        self,
        payload: Any,
        *,
        purpose: str = "pending_action_parameters",
    ) -> bytes:
        if not purpose or len(purpose) > 128:
            raise ValueError("invalid payload purpose")
        envelope = {"payload": payload, "purpose": purpose, "version": 1}
        return self._fernet.encrypt(canonical_json_bytes(envelope))

    def decrypt_json(
        self,
        ciphertext: bytes,
        *,
        purpose: str = "pending_action_parameters",
        ttl_seconds: int | None = None,
    ) -> Any:
        if not ciphertext:
            raise PayloadDecryptionError("encrypted payload is invalid")
        try:
            plaintext = self._fernet.decrypt(ciphertext, ttl=ttl_seconds)
            envelope = json.loads(plaintext.decode("utf-8"))
        except (
            InvalidToken,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise PayloadDecryptionError("encrypted payload is invalid") from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("version") != 1
            or envelope.get("purpose") != purpose
            or "payload" not in envelope
        ):
            raise PayloadDecryptionError("encrypted payload has an invalid envelope")
        payload = envelope["payload"]
        try:
            _validate_json(payload)
        except ValueError as exc:
            raise PayloadDecryptionError("decrypted payload is invalid") from exc
        return payload
