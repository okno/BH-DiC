from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from bh_dic.bridge.contracts import (
    BackendResult,
    BoundedJsonObject,
    BridgeResponse,
    BridgeStatus,
)
from bh_dic.bridge.model_output import (
    BridgeModelOutput,
    PublicHrBridgeOutput,
    output_model_for_task,
)
from bh_dic.bridge.planner import BridgePlanningRouter
from bh_dic.bridge.public_hr import BridgePublicHrResponder
from bh_dic.openai.client import IntentProviderError
from bh_dic.openai.schemas import ProviderTokenUsage
from bh_dic.query.decision import PlanningDecision

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000002")


def test_bridge_selects_one_task_specific_output_schema() -> None:
    planning = BoundedJsonObject.model_validate({"task": "hr_planning"})
    public = BoundedJsonObject.model_validate({"task": "public_hr_response"})

    assert output_model_for_task(BridgeModelOutput, planning) is PlanningDecision
    assert output_model_for_task(BridgeModelOutput, public) is PublicHrBridgeOutput
    with pytest.raises(ValueError, match="unsupported task"):
        output_model_for_task(
            BridgeModelOutput,
            BoundedJsonObject.model_validate({"task": "unknown"}),
        )


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[BoundedJsonObject] = []

    async def plan(
        self,
        payload: BoundedJsonObject,
        *,
        timeout_seconds: float,
    ) -> BridgeResponse:
        assert timeout_seconds == 5
        self.requests.append(payload)
        return BridgeResponse(
            request_id=REQUEST_ID,
            nonce="A" * 32,
            completed_at_ms=1,
            status=BridgeStatus.OK,
            result=BackendResult(
                payload=BoundedJsonObject.model_validate(self.payload),
                backend="codex",
                model="synthetic-model",
                usage=ProviderTokenUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
            ),
        )

    async def close(self) -> None:
        return None


def _intent(function_id: str = "EMP-PAY-001") -> dict[str, object]:
    return {
        "kind": "single_intent",
        "intent": {
            "intent": "payroll_lookup",
            "function_id": function_id,
            "action_class": "READ",
            "employee_id": None,
            "query": None,
            "parameters": {},
            "date_from": None,
            "date_to": None,
            "requires_clarification": False,
            "clarification_question": None,
            "sensitivity": "HIGH",
            "confidence": 0.95,
        },
    }


@pytest.mark.asyncio
async def test_bridge_planner_accepts_only_exposed_catalog_decision() -> None:
    client = _Client(_intent())
    router = BridgePlanningRouter(client, timeout_seconds=5)

    routed = await router.decide(
        "request payroll calendar_month [TERM_REDACTED]",
        frozenset({"EMP-PAY-001"}),
        today=date(2026, 10, 9),
    )

    assert routed.decision.kind == "single_intent"
    assert routed.metadata.usage is not None
    sent = client.requests[0].root
    assert sent["task"] == "hr_planning"
    assert sent["current_date"] == "2026-10-09"
    assert sent["functions"][0]["function_id"] == "EMP-PAY-001"


@pytest.mark.asyncio
async def test_bridge_planner_can_return_unsupported_with_no_visible_function() -> None:
    client = _Client(
        {
            "kind": "unsupported",
            "code": "no_visible_function",
            "reason": "No policy-visible function matches the request.",
        }
    )
    router = BridgePlanningRouter(client, timeout_seconds=5)

    routed = await router.decide(
        "request [TERM_REDACTED]",
        frozenset(),
        today=date(2026, 10, 9),
    )

    assert routed.decision.kind == "unsupported"
    assert client.requests[0].root["functions"] == []


@pytest.mark.asyncio
async def test_bridge_planner_rejects_unexposed_function_and_identity_material() -> None:
    client = _Client(_intent("EMP-READ-002"))
    router = BridgePlanningRouter(client, timeout_seconds=5)
    with pytest.raises(IntentProviderError):
        await router.decide(
            "request payroll",
            frozenset({"EMP-PAY-001"}),
            today=date(2026, 10, 9),
        )

    payload = _intent()
    assert isinstance(payload["intent"], dict)
    payload["intent"]["employee_id"] = "EMP-SYNTH-001"
    router = BridgePlanningRouter(_Client(payload), timeout_seconds=5)
    with pytest.raises(IntentProviderError):
        await router.decide(
            "request payroll",
            frozenset({"EMP-PAY-001"}),
            today=date(2026, 10, 9),
        )


@pytest.mark.asyncio
async def test_bridge_planner_rejects_non_minimized_request_before_transport() -> None:
    client = _Client(_intent())
    router = BridgePlanningRouter(client, timeout_seconds=5)

    with pytest.raises(IntentProviderError):
        await router.decide(
            "request payroll Nora",
            frozenset({"EMP-PAY-001"}),
            today=date(2026, 10, 9),
        )

    assert client.requests == []


@pytest.mark.asyncio
async def test_bridge_public_hr_minimizes_input_and_redacts_output() -> None:
    client = _Client(
        {
            "kind": "public_hr_response",
            "text": "Consulta https://example.invalid e scrivi a @everyone.",
        }
    )
    responder = BridgePublicHrResponder(client, timeout_seconds=5)

    response = await responder.respond("Come funziona la policy ferie?")

    assert "https://" not in response.text
    assert "@everyone" not in response.text
    assert client.requests[0].root["task"] == "public_hr_response"
