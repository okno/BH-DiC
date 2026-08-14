"""Strict schemas shared by intent-router implementations."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictStr,
    field_validator,
    model_validator,
)

from bh_dic.policies.catalog import (
    ALL_FUNCTION_IDS as POLICY_FUNCTION_IDS,
)
from bh_dic.policies.catalog import (
    READ_FUNCTION_IDS as POLICY_READ_FUNCTION_IDS,
)
from bh_dic.policies.catalog import (
    WRITE_FUNCTION_IDS as POLICY_WRITE_FUNCTION_IDS,
)


class ActionClass(StrEnum):
    READ = "READ"
    SEARCH = "SEARCH"
    FILTER = "FILTER"
    PREPARE_WRITE = "PREPARE_WRITE"
    FILE_UPLOAD = "FILE_UPLOAD"
    EXPORT = "EXPORT"
    UNSUPPORTED = "UNSUPPORTED"


class Sensitivity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


READ_FUNCTION_IDS = frozenset(POLICY_READ_FUNCTION_IDS)
WRITE_FUNCTION_IDS = frozenset(POLICY_WRITE_FUNCTION_IDS)
ALL_FUNCTION_IDS = frozenset(POLICY_FUNCTION_IDS) | {"UNSUPPORTED"}

_EMPLOYEE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_INTENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class IntentEnvelope(BaseModel):
    """Validated result of natural-language interpretation.

    ``parameters`` is intentionally JSON-like and is validated recursively so a
    model cannot smuggle executable objects or unexpectedly large payloads into
    the deterministic application layer.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    intent: StrictStr = Field(min_length=1, max_length=64)
    function_id: StrictStr = Field(min_length=1, max_length=32)
    action_class: ActionClass
    employee_id: StrictStr | None = Field(default=None, max_length=64)
    query: StrictStr | None = Field(default=None, max_length=500)
    parameters: dict[StrictStr, Any] = Field(default_factory=dict)
    date_from: date | None = None
    date_to: date | None = None
    requires_clarification: StrictBool = False
    clarification_question: StrictStr | None = Field(default=None, max_length=300)
    sensitivity: Sensitivity
    confidence: StrictFloat = Field(ge=0.0, le=1.0)

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: str) -> str:
        if not _INTENT_RE.fullmatch(value):
            raise ValueError("intent must be a lower_snake_case identifier")
        return value

    @field_validator("function_id")
    @classmethod
    def validate_function_id(cls, value: str) -> str:
        if value not in ALL_FUNCTION_IDS:
            raise ValueError("unknown function_id")
        return value

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str | None) -> str | None:
        if value is not None and not _EMPLOYEE_ID_RE.fullmatch(value):
            raise ValueError("invalid employee_id")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("too many parameters")

        def check(item: Any, *, depth: int = 0) -> None:
            if depth > 4:
                raise ValueError("parameters are too deeply nested")
            if item is None or isinstance(item, bool | int | float):
                return
            if isinstance(item, str):
                if len(item) > 1_000:
                    raise ValueError("parameter string is too long")
                return
            if isinstance(item, list):
                if len(item) > 50:
                    raise ValueError("parameter list is too long")
                for child in item:
                    check(child, depth=depth + 1)
                return
            if isinstance(item, dict):
                if len(item) > 32:
                    raise ValueError("parameter object is too large")
                for key, child in item.items():
                    if not isinstance(key, str) or len(key) > 64:
                        raise ValueError("invalid parameter key")
                    check(child, depth=depth + 1)
                return
            raise ValueError("parameters must contain JSON-compatible values")

        check(value)
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> IntentEnvelope:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        if self.requires_clarification and not self.clarification_question:
            raise ValueError("clarification_question is required")
        if not self.requires_clarification and self.clarification_question:
            raise ValueError("clarification_question requires the clarification flag")
        if self.function_id == "UNSUPPORTED" and self.action_class != ActionClass.UNSUPPORTED:
            raise ValueError("unsupported function must use UNSUPPORTED action class")
        if self.function_id in READ_FUNCTION_IDS and self.action_class == ActionClass.PREPARE_WRITE:
            raise ValueError("read function cannot prepare a write")
        if self.function_id in WRITE_FUNCTION_IDS and self.action_class not in {
            ActionClass.PREPARE_WRITE,
            ActionClass.FILE_UPLOAD,
            ActionClass.EXPORT,
        }:
            raise ValueError("write function has an invalid action class")
        return self


class RouteMetadata(BaseModel):
    """Non-sensitive provider metadata suitable for local observability."""

    model_config = ConfigDict(extra="forbid")

    provider: StrictStr
    model: StrictStr
    request_id: StrictStr | None = None
    tool_name: StrictStr
