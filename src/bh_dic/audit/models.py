"""Strict audit event schemas."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

_FORBIDDEN_AUDIT_KEY = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie|totp|iban|codice.?fiscale|"
    r"tax.?id|e-?mail|phone|telefono|address|indirizzo|first.?name|last.?name|nome|"
    r"cognome|birth|nascita|document.?content|file.?content|storage.?state|full.?prompt)"
)


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNCERTAIN = "UNCERTAIN"


def _event_id() -> str:
    return str(uuid4())


class AuditEventInput(BaseModel):
    """A redacted event accepted by :class:`AuditService`."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    event_id: str = Field(default_factory=_event_id)
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    correlation_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    actor_discord_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    guild_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    channel_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    function_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
    target_pseudonym: str | None = Field(
        default=None, max_length=80, pattern=r"^emp_[a-f0-9]{8,64}$"
    )
    outcome: AuditOutcome
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("event_id must be a UUID") from exc

    @field_validator("timestamp_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def reject_sensitive_payload_keys(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if _FORBIDDEN_AUDIT_KEY.search(str(key)):
                        raise ValueError(f"sensitive audit payload key is forbidden: {key}")
                    visit(nested)
            elif isinstance(item, list):
                for nested in item:
                    visit(nested)

        visit(value)
        return value


class AuditEventMaterial(AuditEventInput):
    """Canonical material covered by the event HMAC."""

    sequence: int = Field(ge=1)
    previous_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AuditEventView(AuditEventMaterial):
    event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AuditVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    event_count: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    last_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    failure_sequence: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=256)
