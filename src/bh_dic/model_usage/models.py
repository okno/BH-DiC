"""Strict, privacy-minimized domain models for provider usage telemetry."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from bh_dic.openai.schemas import ProviderTokenUsage

_TOKEN_PREFIX = re.compile(r"(?i)^(?:bearer|sk-|gsk_|gh[pousr]_|github_pat_|xox[baprs]-)")


class ModelUsageStatus(StrEnum):
    STARTED = "STARTED"
    REPORTED = "REPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ModelUsageKey(BaseModel):
    """Idempotency key for one logical model call within an application request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    correlation_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    purpose: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    ordinal: StrictInt = Field(default=1, ge=1)


class ModelUsageStart(BaseModel):
    """Non-sensitive metadata recorded before contacting a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    key: ModelUsageKey
    provider: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    model: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/+:-]*$",
    )

    @field_validator("model")
    @classmethod
    def reject_token_like_model(cls, value: str) -> str:
        if _TOKEN_PREFIX.match(value):
            raise ValueError("model must not contain token-like material")
        return value


class ModelUsageEvent(BaseModel):
    """One persisted call; terminal events are immutable through the repository API."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    usage_id: str = Field(default_factory=lambda: str(uuid4()))
    key: ModelUsageKey
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=120)
    status: ModelUsageStatus
    usage: ProviderTokenUsage | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @field_validator("usage_id")
    @classmethod
    def validate_usage_id(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("usage_id must be a UUID") from exc

    @field_validator("created_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("usage timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> ModelUsageEvent:
        if self.status is ModelUsageStatus.STARTED:
            if self.completed_at is not None or self.usage is not None:
                raise ValueError("STARTED usage must not be completed")
            return self
        if self.completed_at is None:
            raise ValueError("terminal usage requires completed_at")
        if self.completed_at < self.created_at:
            raise ValueError("completed_at cannot precede created_at")
        if self.status is ModelUsageStatus.REPORTED:
            if self.usage is None:
                raise ValueError("REPORTED usage requires exact counters")
        elif self.usage is not None:
            raise ValueError("unreported usage must not contain counters")
        return self


class ModelUsageTotals(BaseModel):
    """Aggregate of exact reported counters plus explicit telemetry gaps."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    total_calls: StrictInt = Field(ge=0)
    started_calls: StrictInt = Field(ge=0)
    reported_calls: StrictInt = Field(ge=0)
    unavailable_calls: StrictInt = Field(ge=0)
    unknown_calls: StrictInt = Field(ge=0)
    usage: ProviderTokenUsage
    first_recorded_at: datetime | None = None
    last_completed_at: datetime | None = None

    @field_validator("first_recorded_at", "last_completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aggregate timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_call_counts(self) -> ModelUsageTotals:
        classified = (
            self.started_calls + self.reported_calls + self.unavailable_calls + self.unknown_calls
        )
        if classified != self.total_calls:
            raise ValueError("status call counts must equal total_calls")
        if self.total_calls == 0 and (
            self.first_recorded_at is not None or self.last_completed_at is not None
        ):
            raise ValueError("empty aggregates must not contain timestamps")
        return self


ZERO_TOKEN_USAGE = ProviderTokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)


__all__ = [
    "ZERO_TOKEN_USAGE",
    "ModelUsageEvent",
    "ModelUsageKey",
    "ModelUsageStart",
    "ModelUsageStatus",
    "ModelUsageTotals",
]
