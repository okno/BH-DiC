"""Ubiquiti-side adapter from the secure bridge to the planning protocol."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

from bh_dic.ai.planning import RoutedPlanningDecision
from bh_dic.bridge.client import JsonlBridgeClient
from bh_dic.bridge.contracts import BoundedJsonObject
from bh_dic.bridge.errors import BridgeError
from bh_dic.hr_assistant import is_minimized_hr_router_request
from bh_dic.openai.client import IntentProviderError
from bh_dic.openai.schemas import RouteMetadata
from bh_dic.policies.catalog import get_function_spec
from bh_dic.query.decision import (
    PlanningDecision,
    ReadPlanDecision,
    SingleIntentDecision,
)
from bh_dic.query.execution import QueryPlanExecutionError, registered_read_steps


class PlannerFunctionCandidate(BaseModel):
    """Minimal catalog metadata that contains neither policy state nor HR data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    function_id: StrictStr = Field(min_length=1, max_length=32)
    title: StrictStr = Field(min_length=1, max_length=160)
    action_class: StrictStr = Field(min_length=1, max_length=32)
    sensitivity: StrictStr = Field(min_length=1, max_length=16)
    requires_target: StrictBool
    is_write: StrictBool


class PlannerBridgePayload(BaseModel):
    """Closed, identity-free request sent from Ubiquiti to Mint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: Literal["hr_planning"] = "hr_planning"
    request: StrictStr = Field(min_length=1, max_length=4_000)
    current_date: date
    functions: tuple[PlannerFunctionCandidate, ...] = Field(max_length=8)


def build_planner_payload(
    request: str,
    allowed_function_ids: frozenset[str],
    *,
    today: date,
) -> PlannerBridgePayload:
    candidates: list[PlannerFunctionCandidate] = []
    for function_id in sorted(allowed_function_ids):
        spec = get_function_spec(function_id)
        if spec is None or not spec.expose_to_model:
            raise ValueError("planner received a non-exposable Function ID")
        candidates.append(
            PlannerFunctionCandidate(
                function_id=spec.function_id,
                title=spec.title,
                action_class=spec.action_class.value,
                sensitivity=spec.sensitivity.value,
                requires_target=spec.requires_target,
                is_write=spec.is_write,
            )
        )
    return PlannerBridgePayload(
        request=request,
        current_date=today,
        functions=tuple(candidates),
    )


class BridgePlanningRouter:
    """Return only locally revalidated decisions from the Mint planner bridge."""

    def __init__(
        self,
        client: JsonlBridgeClient,
        *,
        timeout_seconds: float,
        configured_model: str = "workspace-default",
    ) -> None:
        if not 0.05 <= timeout_seconds <= 60.0:
            raise ValueError("bridge planner timeout is invalid")
        if not configured_model.strip() or len(configured_model) > 128:
            raise ValueError("bridge planner model label is invalid")
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._configured_model = configured_model

    async def decide(
        self,
        request: str,
        allowed_function_ids: frozenset[str],
        *,
        today: date,
    ) -> RoutedPlanningDecision:
        if not is_minimized_hr_router_request(request):
            raise IntentProviderError(
                "planner request did not cross the local minimization boundary",
                provider="bridge",
                model=self._configured_model,
                response_received=False,
            )
        try:
            payload = build_planner_payload(request, allowed_function_ids, today=today)
            response = await self._client.plan(
                BoundedJsonObject.model_validate(payload.model_dump(mode="json")),
                timeout_seconds=self._timeout_seconds,
            )
        except BridgeError:
            raise IntentProviderError(
                "planner bridge request failed",
                provider="bridge",
                model=self._configured_model,
                response_received=False,
            ) from None
        if response.result is None:
            raise IntentProviderError(
                "planner bridge returned no result",
                provider="bridge",
                model=self._configured_model,
                response_received=True,
            )
        try:
            decision = PlanningDecision.model_validate(response.result.payload.root)
            self._validate_decision(decision, allowed_function_ids)
        except (ValidationError, ValueError, QueryPlanExecutionError):
            raise IntentProviderError(
                "planner bridge returned an invalid decision",
                provider=response.result.backend,
                model=response.result.model,
                usage=response.result.usage,
                response_received=True,
            ) from None
        return RoutedPlanningDecision(
            decision=decision,
            metadata=RouteMetadata(
                provider=response.result.backend,
                model=response.result.model,
                request_id=str(response.request_id),
                tool_name=f"planning_{decision.kind}",
                usage=response.result.usage,
            ),
        )

    @staticmethod
    def _validate_decision(
        decision: PlanningDecision,
        allowed_function_ids: frozenset[str],
    ) -> None:
        root = decision.root
        if isinstance(root, SingleIntentDecision):
            envelope = root.intent
            if envelope.function_id not in allowed_function_ids:
                raise ValueError("planner selected a Function ID outside the exposure scope")
            if envelope.employee_id is not None or envelope.query is not None:
                raise ValueError("planner attempted to return identity material")
            spec = get_function_spec(envelope.function_id)
            if spec is None:
                raise ValueError("planner selected an unknown Function ID")
            if envelope.action_class.value != spec.action_class.value:
                raise ValueError("planner action class does not match the catalog")
            if envelope.sensitivity.value != spec.sensitivity.value:
                raise ValueError("planner sensitivity does not match the catalog")
            if spec.is_write and envelope.parameters:
                raise ValueError("planner cannot originate write parameter values")
            return
        if isinstance(root, ReadPlanDecision):
            function_ids = frozenset(step.function_id for step in root.plan.steps)
            if not function_ids.issubset(allowed_function_ids):
                raise ValueError("planner plan exceeded the Function ID exposure scope")
            registered_read_steps(root.plan)

    async def close(self) -> None:
        await self._client.close()


__all__ = [
    "BridgePlanningRouter",
    "PlannerBridgePayload",
    "PlannerFunctionCandidate",
    "build_planner_payload",
]
