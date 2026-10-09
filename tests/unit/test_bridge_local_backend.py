from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from bh_dic.bridge.backends.openai_compatible import (
    OllamaPlannerBackend,
    OpenAICompatibleRequest,
    OpenAISdkChatTransport,
)
from bh_dic.bridge.contracts import BoundedJsonObject, BridgeRequest
from bh_dic.bridge.errors import BackendOutputError
from bh_dic.query.decision import PlanningDecision


def _request() -> BridgeRequest:
    return BridgeRequest(
        request_id=UUID("00000000-0000-4000-8000-000000000011"),
        nonce="L" * 32,
        issued_at_ms=1_000,
        deadline_ms=5_000,
        payload=BoundedJsonObject.model_validate(
            {
                "task": "hr_planning",
                "request": "employee_headcount request",
            }
        ),
    )


def _client(response: object) -> SimpleNamespace:
    create = AsyncMock(return_value=response)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_local_backend_uses_closed_schema_without_tools_and_reports_exact_usage() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=(
                        '{"kind":"unsupported","code":"general_hr",'
                        '"reason":"No operational function is required."}'
                    ),
                    tool_calls=None,
                    function_call=None,
                    executed_tools=None,
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12, total_tokens=52),
    )
    client = _client(response)
    backend = OllamaPlannerBackend(
        model="synthetic-local-model",
        output_model=PlanningDecision,
        system_instruction="Return a closed HR planning decision.",
        transport=OpenAISdkChatTransport(client),
    )

    result = await backend.plan(_request())

    assert result.backend == "ollama"
    assert result.usage is not None
    assert result.usage.total_tokens == 52
    call = client.chat.completions.create.await_args.kwargs
    assert call["tool_choice"] == "none"
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert "employee_headcount" in call["messages"][1]["content"]

    await backend.close()
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "message"),
    [
        (
            "length",
            SimpleNamespace(
                content='{"kind":"unsupported","code":"x","reason":"truncated"}',
                tool_calls=None,
                function_call=None,
                executed_tools=None,
            ),
        ),
        (
            "stop",
            SimpleNamespace(
                content='{"kind":"unsupported","code":"x","reason":"unsafe"}',
                tool_calls=[{"id": "tool"}],
                function_call=None,
                executed_tools=None,
            ),
        ),
        (
            "stop",
            SimpleNamespace(
                content='{"kind":"unsupported","code":"x","reason":"unsafe"}',
                tool_calls=None,
                function_call=None,
                executed_tools=[{"type": "browser"}],
            ),
        ),
    ],
)
async def test_local_transport_rejects_truncation_and_tool_activity(
    finish_reason: str,
    message: object,
) -> None:
    transport = OpenAISdkChatTransport(
        _client(
            SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])
        )
    )
    request = OpenAICompatibleRequest(
        model="synthetic-local-model",
        system_instruction="Return JSON.",
        payload_json='{"task":"hr_planning"}',
        output_schema=PlanningDecision.model_json_schema(),
        timeout_seconds=1.0,
    )

    with pytest.raises(BackendOutputError):
        await transport.complete(request)
