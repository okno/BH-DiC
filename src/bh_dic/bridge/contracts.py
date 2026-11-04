"""Strict, provider-neutral wire contracts for the planner bridge."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from bh_dic.openai.schemas import ProviderTokenUsage

BRIDGE_PROTOCOL_VERSION: Literal["bh-dic.bridge.v1"] = "bh-dic.bridge.v1"
MAX_WIRE_LINE_BYTES = 65_536
MAX_REQUEST_LIFETIME_MS = 60_000

_NONCE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_BACKEND_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_JSON_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _validate_json_value(value: Any, *, depth: int, budget: list[int]) -> None:
    if depth > 8:
        raise ValueError("JSON payload is too deeply nested")
    budget[0] += 1
    if budget[0] > 1_024:
        raise ValueError("JSON payload contains too many values")
    if value is None or type(value) in {bool, int, float}:
        return
    if type(value) is str:
        if len(value) > 16_384:
            raise ValueError("JSON string exceeds the local limit")
        return
    if type(value) is list:
        if len(value) > 256:
            raise ValueError("JSON array exceeds the local limit")
        for child in value:
            _validate_json_value(child, depth=depth + 1, budget=budget)
        return
    if type(value) is dict:
        if len(value) > 128:
            raise ValueError("JSON object exceeds the local limit")
        for key, child in value.items():
            if type(key) is not str or _JSON_KEY.fullmatch(key) is None:
                raise ValueError("JSON object contains an invalid key")
            _validate_json_value(child, depth=depth + 1, budget=budget)
        return
    raise ValueError("payload must contain only JSON-compatible values")


class BoundedJsonObject(RootModel[dict[str, Any]]):
    """A recursively bounded JSON object used at the transport boundary."""

    model_config = ConfigDict(frozen=True, strict=True)

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, depth=0, budget=[0])
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_WIRE_LINE_BYTES // 2:
            raise ValueError("JSON payload exceeds the local byte limit")
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.root,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )


class BridgeRequest(BaseModel):
    """One expiring, replay-resistant planner request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol: Literal["bh-dic.bridge.v1"] = BRIDGE_PROTOCOL_VERSION
    request_id: UUID
    nonce: StrictStr = Field(min_length=32, max_length=128)
    issued_at_ms: StrictInt = Field(ge=0)
    deadline_ms: StrictInt = Field(ge=0)
    payload: BoundedJsonObject

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        if _NONCE.fullmatch(value) is None:
            raise ValueError("nonce must be unpadded base64url text")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> BridgeRequest:
        lifetime = self.deadline_ms - self.issued_at_ms
        if lifetime <= 0:
            raise ValueError("deadline must follow issue time")
        if lifetime > MAX_REQUEST_LIFETIME_MS:
            raise ValueError("request lifetime exceeds the protocol maximum")
        return self


class BridgeStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class BridgeErrorCode(StrEnum):
    EXPIRED = "expired"
    REPLAY = "replay"
    OVERLOADED = "overloaded"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_FAILURE = "backend_failure"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INTERNAL_ERROR = "internal_error"


class BackendResult(BaseModel):
    """Validated planner output plus non-sensitive accounting metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    payload: BoundedJsonObject
    backend: StrictStr = Field(min_length=1, max_length=32)
    model: StrictStr = Field(min_length=1, max_length=128)
    usage: ProviderTokenUsage | None = None

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if _BACKEND_NAME.fullmatch(value) is None:
            raise ValueError("invalid backend name")
        return value


class BridgeErrorPayload(BaseModel):
    """Closed error details that never include prompts or provider bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: BridgeErrorCode
    message: StrictStr = Field(min_length=1, max_length=160)


class BridgeResponse(BaseModel):
    """One terminal response, correlated to both request ID and nonce."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol: Literal["bh-dic.bridge.v1"] = BRIDGE_PROTOCOL_VERSION
    request_id: UUID
    nonce: StrictStr = Field(min_length=32, max_length=128)
    completed_at_ms: StrictInt = Field(ge=0)
    status: BridgeStatus
    result: BackendResult | None = None
    error: BridgeErrorPayload | None = None

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        if _NONCE.fullmatch(value) is None:
            raise ValueError("nonce must be unpadded base64url text")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> BridgeResponse:
        if self.status is BridgeStatus.OK and (self.result is None or self.error is not None):
            raise ValueError("successful response must contain only a result")
        if self.status is BridgeStatus.ERROR and (self.error is None or self.result is not None):
            raise ValueError("failed response must contain only an error")
        return self


__all__ = [
    "BRIDGE_PROTOCOL_VERSION",
    "MAX_REQUEST_LIFETIME_MS",
    "MAX_WIRE_LINE_BYTES",
    "BackendResult",
    "BoundedJsonObject",
    "BridgeErrorCode",
    "BridgeErrorPayload",
    "BridgeRequest",
    "BridgeResponse",
    "BridgeStatus",
]
