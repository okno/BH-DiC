"""Bounded evidence tracker for deterministic read-only HR query plans."""

from __future__ import annotations

from dataclasses import dataclass

from bh_dic.query.plan import HRQueryPlan


class QueryPlanExecutionError(RuntimeError):
    """A plan exceeded its budget or did not produce verifiable objective evidence."""


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


__all__ = ["QueryPlanEvidence", "QueryPlanExecutionError", "QueryPlanTracker"]
