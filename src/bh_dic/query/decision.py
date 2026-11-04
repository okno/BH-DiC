"""Canonical, strict decision contract for every HR planner implementation.

The transport/provider boundary may use Codex, Ollama, LM Studio, or a deterministic
local parser. All of them must cross this discriminated contract before the
application can consider a result. A decision can contain exactly one outcome;
provider prose is never interpreted as an executable request.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictStr, model_validator

from bh_dic.openai.schemas import ActionClass, IntentEnvelope
from bh_dic.query.plan import HRQueryPlan


class _DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SingleIntentDecision(_DecisionModel):
    """One already validated catalog intent candidate."""

    kind: Literal["single_intent"]
    intent: IntentEnvelope

    @model_validator(mode="after")
    def validate_executable_candidate(self) -> SingleIntentDecision:
        if self.intent.requires_clarification:
            raise ValueError("single_intent cannot carry a clarification")
        if (
            self.intent.function_id == "UNSUPPORTED"
            or self.intent.action_class is ActionClass.UNSUPPORTED
        ):
            raise ValueError("unsupported intent must use an unsupported decision")
        return self


class ReadPlanDecision(_DecisionModel):
    """One strict, read-only, locally executable query plan candidate."""

    kind: Literal["read_plan"]
    plan: HRQueryPlan

    @model_validator(mode="after")
    def validate_complete_candidate(self) -> ReadPlanDecision:
        if self.plan.clarification_required:
            raise ValueError("read_plan cannot carry a clarification")
        return self


class ClarificationDecision(_DecisionModel):
    """A bounded question when a mandatory local fact is genuinely absent."""

    kind: Literal["clarification"]
    question: StrictStr = Field(min_length=1, max_length=300)


class UnsupportedDecision(_DecisionModel):
    """A non-executable result with bounded machine and human reasons."""

    kind: Literal["unsupported"]
    code: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    reason: StrictStr = Field(min_length=1, max_length=300)


PlanningDecisionVariant = Annotated[
    SingleIntentDecision | ReadPlanDecision | ClarificationDecision | UnsupportedDecision,
    Field(discriminator="kind"),
]


class PlanningDecision(RootModel[PlanningDecisionVariant]):
    """Root model whose JSON representation is the discriminated decision itself."""

    model_config = ConfigDict(frozen=True, strict=True)

    @property
    def kind(self) -> str:
        return self.root.kind


__all__ = [
    "ClarificationDecision",
    "PlanningDecision",
    "PlanningDecisionVariant",
    "ReadPlanDecision",
    "SingleIntentDecision",
    "UnsupportedDecision",
]
