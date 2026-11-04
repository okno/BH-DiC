"""Provider-neutral planner result boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from bh_dic.openai.schemas import RouteMetadata
from bh_dic.query.decision import PlanningDecision


@dataclass(frozen=True, slots=True)
class RoutedPlanningDecision:
    """One schema-validated planning decision plus safe provider metadata."""

    decision: PlanningDecision
    metadata: RouteMetadata


@runtime_checkable
class PlanningRouter(Protocol):
    """Optional richer planner implemented by the Codex/local bridge."""

    async def decide(
        self,
        request: str,
        allowed_function_ids: frozenset[str],
        *,
        today: date,
    ) -> RoutedPlanningDecision: ...

    async def close(self) -> None: ...


__all__ = ["PlanningRouter", "RoutedPlanningDecision"]
