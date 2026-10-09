"""Single strict output schema shared by every bridge backend."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictStr

from bh_dic.bridge.contracts import BoundedJsonObject
from bh_dic.query.decision import PlanningDecision, PlanningDecisionVariant


class PublicHrBridgeOutput(BaseModel):
    """Bounded, data-free answer for non-operational HR conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["public_hr_response"]
    text: StrictStr = Field(min_length=1, max_length=1_500)


BridgeModelOutputVariant = Annotated[
    PlanningDecisionVariant | PublicHrBridgeOutput,
    Field(discriminator="kind"),
]


class BridgeModelOutput(RootModel[BridgeModelOutputVariant]):
    model_config = ConfigDict(frozen=True, strict=True)


def output_model_for_task(
    configured_model: type[BaseModel],
    payload: BoundedJsonObject,
) -> type[BaseModel]:
    """Narrow the shared bridge schema to the exact request task."""

    if configured_model is not BridgeModelOutput:
        return configured_model
    task = payload.root.get("task")
    if task == "hr_planning":
        return PlanningDecision
    if task == "public_hr_response":
        return PublicHrBridgeOutput
    raise ValueError("bridge payload contains an unsupported task")


__all__ = [
    "BridgeModelOutput",
    "BridgeModelOutputVariant",
    "PublicHrBridgeOutput",
    "output_model_for_task",
]
