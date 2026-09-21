from __future__ import annotations

import pytest

from bh_dic.openai.schemas import Sensitivity
from bh_dic.query.execution import QueryPlanExecutionError, QueryPlanTracker
from bh_dic.query.plan import HRQueryAction, HRQueryPlan, HRQueryStep, HRResource


def _plan() -> HRQueryPlan:
    return HRQueryPlan(
        intent="synthetic_join",
        resources=(HRResource.EMPLOYEES, HRResource.PAYROLLS),
        sensitivity=Sensitivity.HIGH,
        steps=(
            HRQueryStep(
                step_id="step_1",
                function_id="EMP-READ-001",
                resource=HRResource.EMPLOYEES,
                action=HRQueryAction.READ,
            ),
            HRQueryStep(
                step_id="step_2",
                function_id="EMP-PAY-002",
                resource=HRResource.PAYROLLS,
                action=HRQueryAction.JOIN,
                depends_on=("step_1",),
            ),
        ),
    )


def test_tracker_returns_complete_objective_evidence() -> None:
    tracker = QueryPlanTracker(_plan(), max_resource_reads=20, max_source_records=10)
    tracker.complete_step("step_1", resource_reads=1, source_records=5)
    tracker.complete_step("step_2", resource_reads=5, result_records=2)

    evidence = tracker.verify()

    assert evidence.complete
    assert evidence.resource_reads == 6
    assert evidence.source_records == 5
    assert evidence.result_records == 2


def test_tracker_fails_closed_on_order_budget_or_partial_objective() -> None:
    wrong_order = QueryPlanTracker(_plan(), max_resource_reads=20, max_source_records=10)
    with pytest.raises(QueryPlanExecutionError, match="step order"):
        wrong_order.complete_step("step_2", resource_reads=1)

    over_budget = QueryPlanTracker(_plan(), max_resource_reads=2, max_source_records=10)
    over_budget.complete_step("step_1", resource_reads=1, source_records=5)
    with pytest.raises(QueryPlanExecutionError, match="resource-read budget"):
        over_budget.complete_step("step_2", resource_reads=5, result_records=2)

    partial = QueryPlanTracker(_plan(), max_resource_reads=20, max_source_records=10)
    partial.complete_step("step_1", resource_reads=1, source_records=5)
    with pytest.raises(QueryPlanExecutionError, match="not fully verified"):
        partial.verify()
