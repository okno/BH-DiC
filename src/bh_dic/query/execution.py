"""Bounded evidence tracker for deterministic read-only HR query plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from bh_dic.query.plan import HRQueryAction, HRQueryPlan, HRResource


class QueryPlanExecutionError(RuntimeError):
    """A plan exceeded its budget or did not produce verifiable objective evidence."""


class DicReadServiceMethod(StrEnum):
    """Closed set of ``DicService`` reads usable by an HR query plan."""

    LIST_EMPLOYEES = "list_employees"
    GET_EMPLOYEE_SUMMARY = "get_employee_summary"
    GET_CONTRACTS = "get_contracts"
    GET_ROLES = "get_roles"
    GET_TIME_ACCESS = "get_time_access"
    GET_MATURATIONS = "get_maturations"
    GET_BALANCE = "get_balance"
    GET_PAYROLL_METADATA = "get_payroll_metadata"
    FIND_EMPLOYEES_WITH_PAYROLL = "find_employees_with_payroll"
    GET_DOCUMENT_METADATA = "get_document_metadata"
    LIST_NOTIFICATIONS = "list_notifications"


@dataclass(frozen=True, slots=True)
class ReadFunctionBinding:
    """One immutable Function ID to deterministic service binding.

    The model never supplies a service method, URL, selector, or callable. It can
    only select a Function ID; this registry owns every executable mapping.
    """

    function_id: str
    resource: HRResource
    service_method: DicReadServiceMethod
    actions: frozenset[HRQueryAction]
    requires_target: bool = False
    filter_fields: frozenset[str] = frozenset()
    aggregations: frozenset[str] = frozenset()


def _binding(
    function_id: str,
    resource: HRResource,
    service_method: DicReadServiceMethod,
    *actions: HRQueryAction,
    requires_target: bool = False,
    filter_fields: frozenset[str] = frozenset(),
    aggregations: frozenset[str] = frozenset(),
) -> ReadFunctionBinding:
    return ReadFunctionBinding(
        function_id=function_id,
        resource=resource,
        service_method=service_method,
        actions=frozenset(actions or (HRQueryAction.READ,)),
        requires_target=requires_target,
        filter_fields=filter_fields,
        aggregations=aggregations,
    )


READ_FUNCTION_REGISTRY: Mapping[str, ReadFunctionBinding] = MappingProxyType(
    {
        binding.function_id: binding
        for binding in (
            _binding(
                "EMP-READ-001",
                HRResource.EMPLOYEES,
                DicReadServiceMethod.LIST_EMPLOYEES,
                HRQueryAction.READ,
                HRQueryAction.FILTER,
                HRQueryAction.SORT,
                HRQueryAction.PROJECT,
                HRQueryAction.AGGREGATE,
                filter_fields=frozenset({"status", "group"}),
                aggregations=frozenset({"count"}),
            ),
            _binding(
                "EMP-SEARCH-001",
                HRResource.EMPLOYEES,
                DicReadServiceMethod.LIST_EMPLOYEES,
                HRQueryAction.SEARCH,
            ),
            _binding(
                "EMP-FILTER-001",
                HRResource.EMPLOYEES,
                DicReadServiceMethod.LIST_EMPLOYEES,
                HRQueryAction.FILTER,
                filter_fields=frozenset({"status", "group"}),
            ),
            _binding(
                "EMP-SORT-001",
                HRResource.EMPLOYEES,
                DicReadServiceMethod.LIST_EMPLOYEES,
                HRQueryAction.SORT,
            ),
            _binding(
                "EMP-PAGE-001",
                HRResource.EMPLOYEES,
                DicReadServiceMethod.LIST_EMPLOYEES,
                HRQueryAction.READ,
            ),
            _binding(
                "EMP-READ-002",
                HRResource.EMPLOYEE_SUMMARY,
                DicReadServiceMethod.GET_EMPLOYEE_SUMMARY,
                HRQueryAction.READ,
                HRQueryAction.PROJECT,
                requires_target=True,
            ),
            _binding(
                "EMP-CONTRACT-001",
                HRResource.CONTRACTS,
                DicReadServiceMethod.GET_CONTRACTS,
                HRQueryAction.READ,
                HRQueryAction.FILTER,
                HRQueryAction.JOIN,
                filter_fields=frozenset({"contract_end_date"}),
            ),
            _binding(
                "EMP-RBAC-001",
                HRResource.ROLES,
                DicReadServiceMethod.GET_ROLES,
                HRQueryAction.READ,
                HRQueryAction.JOIN,
                requires_target=True,
            ),
            _binding(
                "EMP-TIME-001",
                HRResource.TIME_ACCESS,
                DicReadServiceMethod.GET_TIME_ACCESS,
                HRQueryAction.READ,
                HRQueryAction.JOIN,
                requires_target=True,
            ),
            _binding(
                "EMP-MAT-001",
                HRResource.MATURATIONS,
                DicReadServiceMethod.GET_MATURATIONS,
                HRQueryAction.READ,
                HRQueryAction.JOIN,
                requires_target=True,
            ),
            _binding(
                "EMP-BAL-001",
                HRResource.BALANCES,
                DicReadServiceMethod.GET_BALANCE,
                HRQueryAction.READ,
                HRQueryAction.JOIN,
                requires_target=True,
                filter_fields=frozenset({"year"}),
            ),
            _binding(
                "EMP-PAY-001",
                HRResource.PAYROLLS,
                DicReadServiceMethod.GET_PAYROLL_METADATA,
                HRQueryAction.READ,
                HRQueryAction.JOIN,
                requires_target=True,
                filter_fields=frozenset({"payroll_year", "payroll_month"}),
                aggregations=frozenset({"latest_paid", "selected_period"}),
            ),
            _binding(
                "EMP-PAY-002",
                HRResource.PAYROLLS,
                DicReadServiceMethod.FIND_EMPLOYEES_WITH_PAYROLL,
                HRQueryAction.READ,
                HRQueryAction.FILTER,
                HRQueryAction.JOIN,
                filter_fields=frozenset({"payroll", "payroll_year", "payroll_month"}),
            ),
            _binding(
                "EMP-DOC-001",
                HRResource.DOCUMENTS,
                DicReadServiceMethod.GET_DOCUMENT_METADATA,
                HRQueryAction.READ,
                HRQueryAction.FILTER,
                HRQueryAction.JOIN,
                requires_target=True,
                filter_fields=frozenset({"status", "category"}),
            ),
            _binding(
                "EMP-NOTIF-001",
                HRResource.NOTIFICATIONS,
                DicReadServiceMethod.LIST_NOTIFICATIONS,
                HRQueryAction.READ,
                HRQueryAction.FILTER,
                filter_fields=frozenset({"read"}),
            ),
        )
    }
)


def registered_read_steps(plan: HRQueryPlan) -> tuple[ReadFunctionBinding, ...]:
    """Prove that every step has one exact, deterministic local implementation."""

    if plan.clarification_required:
        raise QueryPlanExecutionError("clarification plan is not executable")
    bindings: list[ReadFunctionBinding] = []
    for step in plan.steps:
        binding = READ_FUNCTION_REGISTRY.get(step.function_id)
        if binding is None:
            raise QueryPlanExecutionError("query step has no registered read executor")
        if step.resource is not binding.resource:
            raise QueryPlanExecutionError("query step resource does not match its Function ID")
        if step.action not in binding.actions:
            raise QueryPlanExecutionError("query step action is not registered")
        filter_fields = {item.field for item in step.filters}
        if not filter_fields.issubset(binding.filter_fields):
            raise QueryPlanExecutionError("query step filter is not registered")
        aggregation = step.aggregation
        if aggregation is not None and aggregation not in binding.aggregations:
            raise QueryPlanExecutionError("query step aggregation is not registered")
        if binding.requires_target and step.target_entity is None and not step.depends_on:
            raise QueryPlanExecutionError("query step requires an opaque target")
        if step.target_entity is not None and step.target_entity not in plan.target_entities:
            raise QueryPlanExecutionError("query step target is not declared by the plan")
        bindings.append(binding)
    registered_filter_fields = frozenset().union(*(binding.filter_fields for binding in bindings))
    if not {item.field for item in plan.filters}.issubset(registered_filter_fields):
        raise QueryPlanExecutionError("query plan filter is not registered")
    if plan.aggregation is not None and not any(
        plan.aggregation in binding.aggregations for binding in bindings
    ):
        raise QueryPlanExecutionError("query plan aggregation is not registered")
    return tuple(bindings)


@dataclass(frozen=True, slots=True)
class QueryPlanEvidence:
    plan_intent: str
    completed_steps: tuple[str, ...]
    resource_reads: int
    source_records: int
    result_records: int
    complete: bool


class QueryPlanTracker:
    """Enforce declared step order and finite browser/resource-read budgets."""

    def __init__(
        self,
        plan: HRQueryPlan,
        *,
        max_resource_reads: int,
        max_source_records: int,
    ) -> None:
        if max_resource_reads < 1 or max_source_records < 1:
            raise ValueError("query plan budgets must be positive")
        self._plan_intent = plan.intent
        self._expected = tuple(step.step_id for step in plan.steps)
        self._completed: list[str] = []
        self._max_resource_reads = max_resource_reads
        self._max_source_records = max_source_records
        self._resource_reads = 0
        self._source_records: int | None = None
        self._result_records: int | None = None

    def complete_step(
        self,
        step_id: str,
        *,
        resource_reads: int,
        source_records: int | None = None,
        result_records: int | None = None,
    ) -> None:
        expected_index = len(self._completed)
        if expected_index >= len(self._expected) or self._expected[expected_index] != step_id:
            raise QueryPlanExecutionError("query plan step order changed during execution")
        if resource_reads < 0:
            raise QueryPlanExecutionError("query plan reported an invalid read count")
        self._resource_reads += resource_reads
        if self._resource_reads > self._max_resource_reads:
            raise QueryPlanExecutionError("query plan exceeded the resource-read budget")
        if source_records is not None:
            if source_records < 0 or source_records > self._max_source_records:
                raise QueryPlanExecutionError("query plan exceeded the source-record budget")
            if self._source_records is not None and self._source_records != source_records:
                raise QueryPlanExecutionError("query plan source cardinality changed")
            self._source_records = source_records
        if result_records is not None:
            if result_records < 0 or result_records > self._max_source_records:
                raise QueryPlanExecutionError("query plan result cardinality is invalid")
            self._result_records = result_records
        self._completed.append(step_id)

    def verify(self) -> QueryPlanEvidence:
        if tuple(self._completed) != self._expected:
            raise QueryPlanExecutionError("query plan objective is not fully verified")
        if self._source_records is None or self._result_records is None:
            raise QueryPlanExecutionError("query plan cardinality evidence is incomplete")
        if self._result_records > self._source_records:
            raise QueryPlanExecutionError("query plan result exceeds its verified source")
        return QueryPlanEvidence(
            plan_intent=self._plan_intent,
            completed_steps=tuple(self._completed),
            resource_reads=self._resource_reads,
            source_records=self._source_records,
            result_records=self._result_records,
            complete=True,
        )


__all__ = [
    "READ_FUNCTION_REGISTRY",
    "DicReadServiceMethod",
    "QueryPlanEvidence",
    "QueryPlanExecutionError",
    "QueryPlanTracker",
    "ReadFunctionBinding",
    "registered_read_steps",
]
