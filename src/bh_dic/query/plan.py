"""Strict read-only plan contract for conversational HR requests."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from bh_dic.openai.schemas import Sensitivity
from bh_dic.policies.catalog import READ_FUNCTION_IDS

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STEP_ID = re.compile(r"^step_[1-8]$")
_ENTITY_PLACEHOLDER = re.compile(r"^(?:EMPLOYEE_TARGET|CONTEXT_RESULT)_[1-9][0-9]{0,2}$")


class HRResource(StrEnum):
    EMPLOYEES = "employees"
    EMPLOYEE_SUMMARY = "employee_summary"
    CONTRACTS = "contracts"
    ROLES = "roles"
    TIME_ACCESS = "time_access"
    MATURATIONS = "maturations"
    BALANCES = "balances"
    PAYROLLS = "payrolls"
    DOCUMENTS = "documents"


class HRQueryAction(StrEnum):
    READ = "read"
    SEARCH = "search"
    FILTER = "filter"
    JOIN = "join"
    AGGREGATE = "aggregate"
    SORT = "sort"
    PROJECT = "project"
    EXPORT = "export"


class EntityResolutionMode(StrEnum):
    NONE = "none"
    EXACT_ID = "exact_id"
    LOCAL_SEARCH = "local_search"
    CONTEXT_REFERENCE = "context_reference"


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    CONTAINS = "contains"
    IN = "in"
    BETWEEN = "between"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class OutputFormat(StrEnum):
    EMBED = "embed"
    TABLE = "table"
    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"
    DOCX = "docx"


class DeliveryMode(StrEnum):
    CHANNEL = "channel"
    EPHEMERAL = "ephemeral"
    PRIVATE = "private"
    EXPORT = "export"


class HRDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    date_from: date
    date_to: date
    label: StrictStr = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_order(self) -> HRDateRange:
        if self.date_from > self.date_to:
            raise ValueError("date range is reversed")
        if (self.date_to - self.date_from).days > 3_660:
            raise ValueError("date range exceeds the bounded horizon")
        return self


class HRFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field: StrictStr = Field(min_length=1, max_length=64)
    operator: FilterOperator
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> HRFilter:
        if _IDENTIFIER.fullmatch(self.field) is None:
            raise ValueError("invalid filter field")
        if self.operator in {FilterOperator.EXISTS, FilterOperator.NOT_EXISTS}:
            if self.value is not None:
                raise ValueError("existence filter cannot include a value")
            return self
        if self.value is None:
            raise ValueError("filter value is required")
        if isinstance(self.value, str):
            if not self.value.strip() or len(self.value) > 128:
                raise ValueError("invalid filter value")
        elif isinstance(self.value, bool | int | date):
            pass
        elif isinstance(self.value, list):
            if not 1 <= len(self.value) <= 24 or any(
                not isinstance(item, str | int) for item in self.value
            ):
                raise ValueError("invalid filter collection")
        else:
            raise ValueError("unsupported filter value")
        return self


class HRSort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field: StrictStr = Field(min_length=1, max_length=64)
    direction: str = Field(pattern=r"^(?:asc|desc)$")


class HRPagination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    page: StrictInt = Field(default=1, ge=1, le=10_000)
    page_size: StrictInt = Field(default=25, ge=1, le=100)
    require_complete: StrictBool = True


class HRQueryStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    step_id: StrictStr
    function_id: StrictStr
    resource: HRResource
    action: HRQueryAction
    depends_on: tuple[StrictStr, ...] = ()
    target_entity: StrictStr | None = None
    filters: tuple[HRFilter, ...] = ()
    projection: tuple[StrictStr, ...] = ()
    aggregation: StrictStr | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_step(self) -> HRQueryStep:
        if _STEP_ID.fullmatch(self.step_id) is None:
            raise ValueError("invalid query step identifier")
        if self.function_id not in READ_FUNCTION_IDS:
            raise ValueError("query plans may contain only catalogued read functions")
        if (
            self.target_entity is not None
            and _ENTITY_PLACEHOLDER.fullmatch(self.target_entity) is None
        ):
            raise ValueError("target entity must be an opaque placeholder")
        if len(self.depends_on) > 7 or self.step_id in self.depends_on:
            raise ValueError("invalid query step dependencies")
        if len(self.filters) > 16 or len(self.projection) > 32:
            raise ValueError("query step exceeds bounded fields")
        if any(_IDENTIFIER.fullmatch(field) is None for field in self.projection):
            raise ValueError("invalid projection field")
        return self


class HRQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    intent: StrictStr = Field(min_length=1, max_length=64)
    resources: tuple[HRResource, ...]
    target_entities: tuple[StrictStr, ...] = ()
    entity_resolution: EntityResolutionMode = EntityResolutionMode.NONE
    filters: tuple[HRFilter, ...] = ()
    date_range: HRDateRange | None = None
    joins: tuple[StrictStr, ...] = ()
    projection: tuple[StrictStr, ...] = ()
    aggregation: StrictStr | None = Field(default=None, max_length=64)
    grouping: tuple[StrictStr, ...] = ()
    sorting: tuple[HRSort, ...] = ()
    pagination: HRPagination = Field(default_factory=HRPagination)
    output_format: OutputFormat = OutputFormat.EMBED
    delivery_mode: DeliveryMode = DeliveryMode.EPHEMERAL
    sensitivity: Sensitivity
    clarification_required: StrictBool = False
    clarification_question: StrictStr | None = Field(default=None, max_length=300)
    steps: tuple[HRQueryStep, ...]

    @model_validator(mode="after")
    def validate_plan(self) -> HRQueryPlan:
        if _IDENTIFIER.fullmatch(self.intent) is None:
            raise ValueError("invalid plan intent")
        if not 1 <= len(self.resources) <= 9 or len(set(self.resources)) != len(self.resources):
            raise ValueError("invalid plan resources")
        if not 1 <= len(self.steps) <= 8:
            raise ValueError("invalid plan step count")
        if any(_ENTITY_PLACEHOLDER.fullmatch(item) is None for item in self.target_entities):
            raise ValueError("target entity must be an opaque placeholder")
        if self.clarification_required != (self.clarification_question is not None):
            raise ValueError("clarification state is inconsistent")
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen or any(
                dependency not in seen for dependency in step.depends_on
            ):
                raise ValueError("query plan dependencies are unordered or duplicated")
            seen.add(step.step_id)
            if step.resource not in self.resources:
                raise ValueError("query step resource is undeclared")
        return self
