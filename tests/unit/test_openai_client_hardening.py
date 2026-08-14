from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bh_dic.openai.client import IntentProviderError, ResponsesIntentClient, envelope_from_call
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, Sensitivity


def _tool_arguments(**overrides: object) -> str:
    payload: dict[str, object] = {
        "function_id": "EMP-READ-001",
        "employee_id": None,
        "query": None,
        "parameters_json": "{}",
        "date_from": None,
        "date_to": None,
        "requires_clarification": False,
        "clarification_question": None,
        "sensitivity": "LOW",
        "confidence": 1.0,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _envelope_data(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "intent": "list_employees",
        "function_id": "EMP-READ-001",
        "action_class": ActionClass.READ,
        "employee_id": None,
        "query": None,
        "parameters": {},
        "date_from": None,
        "date_to": None,
        "requires_clarification": False,
        "clarification_question": None,
        "sensitivity": Sensitivity.LOW,
        "confidence": 1.0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("name", "arguments", "allowed", "message"),
    [
        ("list_employees", "x" * 8_001, frozenset({"EMP-READ-001"}), "exceed"),
        ("list_employees", "{", frozenset({"EMP-READ-001"}), "valid JSON"),
        ("list_employees", "[]", frozenset({"EMP-READ-001"}), "must be an object"),
        ("not_a_tool", _tool_arguments(), frozenset({"EMP-READ-001"}), "unknown tool"),
        (
            "list_employees",
            _tool_arguments(function_id="EMP-READ-002"),
            frozenset({"EMP-READ-001"}),
            "non-exposed",
        ),
        (
            "list_employees",
            _tool_arguments(sensitivity=7),
            frozenset({"EMP-READ-001"}),
            "invalid sensitivity",
        ),
        (
            "list_employees",
            _tool_arguments(confidence=True),
            frozenset({"EMP-READ-001"}),
            "invalid confidence",
        ),
        (
            "list_employees",
            _tool_arguments(parameters_json={}),
            frozenset({"EMP-READ-001"}),
            "invalid parameters_json",
        ),
        (
            "list_employees",
            _tool_arguments(parameters_json="{"),
            frozenset({"EMP-READ-001"}),
            "malformed parameters_json",
        ),
        (
            "list_employees",
            _tool_arguments(parameters_json="[]"),
            frozenset({"EMP-READ-001"}),
            "must encode an object",
        ),
        (
            "list_employees",
            _tool_arguments(parameters_json="x" * 4_001),
            frozenset({"EMP-READ-001"}),
            "invalid parameters_json",
        ),
        (
            "list_employees",
            _tool_arguments(date_from=20260814),
            frozenset({"EMP-READ-001"}),
            "non-string date",
        ),
        (
            "list_employees",
            _tool_arguments(date_to="14/08/2026"),
            frozenset({"EMP-READ-001"}),
            "invalid ISO date",
        ),
        (
            "list_employees",
            _tool_arguments(employee_id="contains spaces"),
            frozenset({"EMP-READ-001"}),
            "local validation",
        ),
    ],
)
def test_envelope_from_call_rejects_malformed_provider_output(
    name: str,
    arguments: str,
    allowed: frozenset[str],
    message: str,
) -> None:
    with pytest.raises(IntentProviderError, match=message):
        envelope_from_call(name, arguments, allowed)


def test_envelope_from_call_supports_null_parameters_dates_and_unsupported() -> None:
    routed = envelope_from_call(
        "list_employees",
        _tool_arguments(
            parameters_json=None,
            date_from="2026-08-01",
            date_to="2026-08-31",
        ),
        frozenset({"EMP-READ-001"}),
    )
    unsupported = envelope_from_call(
        "unsupported_request",
        _tool_arguments(function_id="UNSUPPORTED"),
        frozenset(),
    )

    assert routed.parameters == {}
    assert routed.date_from == date(2026, 8, 1)
    assert routed.date_to == date(2026, 8, 31)
    assert unsupported.function_id == "UNSUPPORTED"
    assert unsupported.action_class is ActionClass.UNSUPPORTED

    valid_list = IntentEnvelope.model_validate(_envelope_data(parameters={"values": [1, 2, 3]}))
    assert valid_list.parameters == {"values": [1, 2, 3]}


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "Not_Snake"},
        {"employee_id": "bad employee"},
        {"clarification_question": "Non richiesta"},
        {"function_id": "UNSUPPORTED", "action_class": ActionClass.READ},
        {"action_class": ActionClass.PREPARE_WRITE},
        {"function_id": "EMP-UPDATE-001", "action_class": ActionClass.READ},
        {"parameters": {f"key_{index}": index for index in range(33)}},
        {"parameters": {"value": "x" * 1_001}},
        {"parameters": {"value": list(range(51))}},
        {"parameters": {"value": {f"key_{index}": index for index in range(33)}}},
        {"parameters": {"value": {"x" * 65: "invalid"}}},
        {"parameters": {"value": [[[[["too deep"]]]]]}},
        {"parameters": {"value": {1, 2}}},
    ],
)
def test_intent_envelope_rejects_schema_boundary_violations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        IntentEnvelope.model_validate(_envelope_data(**overrides))


class _ResponsesStub:
    def __init__(
        self,
        *,
        output: list[object] | None = None,
        error: Exception | None = None,
        request_id: str | None = None,
    ) -> None:
        self.output = output or []
        self.error = error
        self.request_id = request_id
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        response = SimpleNamespace(output=self.output)
        if self.request_id is not None:
            response._request_id = self.request_id
        return response


def _client(responses: _ResponsesStub, *, reasoning_effort: str = "low") -> ResponsesIntentClient:
    return ResponsesIntentClient(
        api_key="synthetic-provider-key",
        model="synthetic-model",
        reasoning_effort=reasoning_effort,
        provider=SimpleNamespace(responses=responses),
    )


def test_responses_client_requires_key_and_model() -> None:
    provider = SimpleNamespace(responses=_ResponsesStub())
    with pytest.raises(ValueError, match="API key"):
        ResponsesIntentClient(api_key="", model="synthetic", provider=provider)
    with pytest.raises(ValueError, match="model"):
        ResponsesIntentClient(api_key="synthetic", model="", provider=provider)


@pytest.mark.asyncio
async def test_responses_client_can_build_default_sdk_boundary_without_network() -> None:
    client = ResponsesIntentClient(
        api_key="synthetic-provider-key",
        model="synthetic-model",
        timeout_seconds=1,
        max_retries=0,
    )
    provider = cast(Any, client._provider)
    await provider.close()


@pytest.mark.asyncio
async def test_responses_client_normalizes_provider_failures_and_call_count() -> None:
    failing = _client(_ResponsesStub(error=RuntimeError("provider detail must stay private")))
    with pytest.raises(IntentProviderError, match="routing failed"):
        await failing.route("richiesta sintetica", frozenset({"EMP-READ-001"}))

    no_call = _client(_ResponsesStub(output=[SimpleNamespace(type="message")]))
    with pytest.raises(IntentProviderError, match="exactly one"):
        await no_call.route("richiesta sintetica", frozenset({"EMP-READ-001"}))

    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )
    duplicate = _client(_ResponsesStub(output=[call, call]))
    with pytest.raises(IntentProviderError, match="exactly one"):
        await duplicate.route("richiesta sintetica", frozenset({"EMP-READ-001"}))


@pytest.mark.asyncio
async def test_responses_client_omits_reasoning_when_disabled_and_handles_missing_request_id() -> (
    None
):
    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )
    responses = _ResponsesStub(output=[call])
    result = await _client(responses, reasoning_effort="").route(
        "richiesta sintetica", frozenset({"EMP-READ-001"})
    )

    assert result.envelope.function_id == "EMP-READ-001"
    assert result.metadata.request_id is None
    assert responses.request is not None
    assert "reasoning" not in responses.request
    assert responses.request["store"] is False
