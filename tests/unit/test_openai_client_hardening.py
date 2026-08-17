from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bh_dic.openai.client import (
    GroqResponsesIntentClient,
    IntentProviderError,
    LlamaChatCompletionsIntentClient,
    ResponsesIntentClient,
    envelope_from_call,
)
from bh_dic.openai.providers import GROQ_OPENAI_BASE_URL, OPENAI_RESPONSES_BASE_URL
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, ProviderTokenUsage, Sensitivity


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


def test_malformed_provider_json_never_survives_in_exception_chains() -> None:
    private_marker = "PRIVATE_PROVIDER_DOCUMENT_MARKER"
    malformed = f'{{"private":"{private_marker}"'

    with pytest.raises(IntentProviderError, match="valid JSON") as caught:
        envelope_from_call("list_employees", malformed, frozenset({"EMP-READ-001"}))

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_marker not in repr(caught.value)


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
        usage: object | None = None,
    ) -> None:
        self.output = output or []
        self.error = error
        self.request_id = request_id
        self.usage = usage
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        response = SimpleNamespace(output=self.output, usage=self.usage)
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
async def test_responses_client_pins_official_sdk_origin_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example.invalid/v1")
    client = ResponsesIntentClient(
        api_key="synthetic-provider-key",
        model="synthetic-model",
        timeout_seconds=1,
        max_retries=0,
    )
    provider = cast(Any, client._provider)
    assert str(provider.base_url).rstrip("/") == OPENAI_RESPONSES_BASE_URL
    assert provider._client.follow_redirects is False
    assert provider._client.trust_env is False
    await provider.close()


@pytest.mark.asyncio
async def test_responses_client_normalizes_provider_failures_and_call_count() -> None:
    reflected = "EMP-SYNTH-001 Mario Rossi must stay private"
    failing = _client(_ResponsesStub(error=RuntimeError(reflected)))
    with pytest.raises(IntentProviderError, match="routing failed") as caught:
        await failing.route("richiesta sintetica", frozenset({"EMP-READ-001"}))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert reflected not in str(caught.value)
    assert caught.value.response_received is False
    assert caught.value.usage is None

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
async def test_responses_client_preserves_exact_usage_on_success_and_invalid_tool_output() -> None:
    usage = SimpleNamespace(input_tokens=101, output_tokens=23, total_tokens=124)
    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )
    routed = await _client(_ResponsesStub(output=[call], usage=usage)).route(
        "richiesta sintetica", frozenset({"EMP-READ-001"})
    )

    assert routed.metadata.usage == ProviderTokenUsage(
        input_tokens=101,
        output_tokens=23,
        total_tokens=124,
    )

    invalid = _client(_ResponsesStub(output=[SimpleNamespace(type="message")], usage=usage))
    with pytest.raises(IntentProviderError, match="exactly one") as caught:
        await invalid.route("richiesta sintetica", frozenset({"EMP-READ-001"}))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.response_received is True
    assert caught.value.provider == "openai"
    assert caught.value.model == "synthetic-model"
    assert caught.value.usage == routed.metadata.usage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": True, "output_tokens": 1, "total_tokens": 2},
        {"input_tokens": "1", "output_tokens": 1, "total_tokens": 2},
        {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 3},
        {"input_tokens": 1, "output_tokens": 1},
    ],
)
async def test_responses_client_fails_closed_on_malformed_present_usage(usage: object) -> None:
    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )

    with pytest.raises(IntentProviderError, match="token usage") as caught:
        await _client(_ResponsesStub(output=[call], usage=usage)).route(
            "richiesta sintetica", frozenset({"EMP-READ-001"})
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.response_received is True
    assert caught.value.usage is None


@pytest.mark.asyncio
async def test_private_usage_property_failure_is_detached_from_sanitized_error() -> None:
    private_marker = "PRIVATE_USAGE_PROPERTY_MARKER"

    class PrivateUsage:
        @property
        def input_tokens(self) -> int:
            raise RuntimeError(private_marker)

    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )
    with pytest.raises(IntentProviderError, match="token usage") as caught:
        await _client(_ResponsesStub(output=[call], usage=PrivateUsage())).route(
            "richiesta sintetica", frozenset({"EMP-READ-001"})
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_marker not in repr(caught.value)


def test_provider_token_usage_schema_is_strict_and_consistent() -> None:
    assert ProviderTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5).total_tokens == 5
    for payload in (
        {"input_tokens": False, "output_tokens": 0, "total_tokens": 0},
        {"input_tokens": 0, "output_tokens": "0", "total_tokens": 0},
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": -1},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 1},
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "private": "x"},
    ):
        with pytest.raises(ValidationError):
            ProviderTokenUsage.model_validate(payload)


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
    assert result.metadata.usage is None
    assert responses.request is not None
    assert "reasoning" not in responses.request
    assert responses.request["store"] is False


@pytest.mark.asyncio
async def test_groq_responses_client_uses_same_closed_validation_and_omits_none_reasoning() -> None:
    call = SimpleNamespace(
        type="function_call",
        name="list_employees",
        arguments=_tool_arguments(),
    )
    responses = _ResponsesStub(output=[call], request_id="req-groq-synthetic")
    client = GroqResponsesIntentClient(
        api_key="synthetic-groq-key",
        model="openai/gpt-oss-120b",
        reasoning_effort="none",
        developer_prompt="Prompt sintetico chiuso.",
        provider=SimpleNamespace(responses=responses),
    )

    result = await client.route("richiesta sintetica", frozenset({"EMP-READ-001"}))

    assert result.metadata.provider == "groq"
    assert result.metadata.request_id == "req-groq-synthetic"
    assert responses.request is not None
    assert responses.request["store"] is False
    assert "reasoning" not in responses.request
    assert responses.request["input"][0]["content"] == "Prompt sintetico chiuso."


@pytest.mark.asyncio
async def test_groq_sdk_boundary_is_pinned_to_official_origin_without_network() -> None:
    client = GroqResponsesIntentClient(
        api_key="synthetic-groq-key",
        model="openai/gpt-oss-120b",
        timeout_seconds=1,
        max_retries=0,
    )
    provider = cast(Any, client._provider)
    assert str(provider.base_url).rstrip("/") == GROQ_OPENAI_BASE_URL
    assert provider._client.follow_redirects is False
    assert provider._client.trust_env is False
    await provider.close()


class _ChatCompletionsStub:
    def __init__(
        self,
        *,
        choices: list[object] | None = None,
        error: Exception | None = None,
        request_id: str | None = None,
        usage: object | None = None,
    ) -> None:
        self.choices = choices or []
        self.error = error
        self.request_id = request_id
        self.usage = usage
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        response = SimpleNamespace(choices=self.choices, usage=self.usage)
        if self.request_id is not None:
            response._request_id = self.request_id
        return response


def _chat_choice(*calls: object) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(tool_calls=list(calls)))


def _chat_call(
    *, name: str = "list_employees", arguments: str | None = None, call_type: str = "function"
) -> SimpleNamespace:
    return SimpleNamespace(
        type=call_type,
        function=SimpleNamespace(name=name, arguments=arguments or _tool_arguments()),
    )


def _llama_client(completions: _ChatCompletionsStub) -> LlamaChatCompletionsIntentClient:
    return LlamaChatCompletionsIntentClient(
        model="synthetic-llama-model",
        base_url="http://127.0.0.1:11434/v1",
        developer_prompt="Prompt llama sintetico.",
        provider=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


@pytest.mark.asyncio
async def test_llama_sdk_boundary_disables_redirects_without_network() -> None:
    client = LlamaChatCompletionsIntentClient(
        model="synthetic-llama-model",
        base_url="http://127.0.0.1:11434/v1",
        timeout_seconds=1,
        max_retries=0,
    )
    provider = cast(Any, client._provider)
    assert provider._client.follow_redirects is False
    assert provider._client.trust_env is False
    await provider.close()


@pytest.mark.asyncio
async def test_llama_provider_failure_drops_private_exception_chain() -> None:
    reflected = "EMP-SYNTH-001 Mario Rossi must stay private"
    client = _llama_client(_ChatCompletionsStub(error=RuntimeError(reflected)))

    with pytest.raises(IntentProviderError, match="llama intent routing failed") as caught:
        await client.route("richiesta sintetica", frozenset({"EMP-READ-001"}))

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert reflected not in str(caught.value)


@pytest.mark.asyncio
async def test_llama_chat_client_uses_interoperable_tools_and_exact_local_validation() -> None:
    completions = _ChatCompletionsStub(
        choices=[_chat_choice(_chat_call())],
        request_id="req-llama-synthetic",
        usage=SimpleNamespace(prompt_tokens=71, completion_tokens=12, total_tokens=83),
    )

    result = await _llama_client(completions).route(
        "richiesta sintetica", frozenset({"EMP-READ-001"})
    )

    assert result.envelope.function_id == "EMP-READ-001"
    assert result.metadata.provider == "llama"
    assert result.metadata.request_id == "req-llama-synthetic"
    assert result.metadata.usage == ProviderTokenUsage(
        input_tokens=71,
        output_tokens=12,
        total_tokens=83,
    )
    assert completions.request is not None
    assert completions.request["messages"][0] == {
        "role": "system",
        "content": "Prompt llama sintetico.",
    }
    assert completions.request["tool_choice"] == "required"
    assert completions.request["tools"][0]["function"]["strict"] is True
    assert "parallel_tool_calls" not in completions.request
    assert "store" not in completions.request
    assert "reasoning_effort" not in completions.request


@pytest.mark.asyncio
async def test_llama_preserves_chat_usage_when_tool_validation_fails() -> None:
    usage = SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13)
    client = _llama_client(_ChatCompletionsStub(choices=[], usage=usage))

    with pytest.raises(IntentProviderError, match="exactly one completion") as caught:
        await client.route("richiesta sintetica", frozenset({"EMP-READ-001"}))

    assert caught.value.response_received is True
    assert caught.value.provider == "llama"
    assert caught.value.usage == ProviderTokenUsage(
        input_tokens=8,
        output_tokens=5,
        total_tokens=13,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choices", "message"),
    [
        ([], "exactly one completion"),
        ([_chat_choice()], "exactly one tool call"),
        ([_chat_choice(_chat_call(), _chat_call())], "exactly one tool call"),
        ([_chat_choice(_chat_call(call_type="custom"))], "non-function"),
    ],
)
async def test_llama_chat_client_rejects_ambiguous_or_invalid_calls(
    choices: list[object], message: str
) -> None:
    client = _llama_client(_ChatCompletionsStub(choices=choices))

    with pytest.raises(IntentProviderError, match=message):
        await client.route("richiesta sintetica", frozenset({"EMP-READ-001"}))


def test_provider_clients_validate_prompt_and_remote_llama_credentials() -> None:
    responses_provider = SimpleNamespace(responses=_ResponsesStub())
    with pytest.raises(ValueError, match="developer prompt"):
        ResponsesIntentClient(
            api_key="synthetic",
            model="synthetic",
            developer_prompt="   ",
            provider=responses_provider,
        )
    with pytest.raises(ValueError, match=r"API key.*remote"):
        LlamaChatCompletionsIntentClient(
            model="synthetic",
            base_url="https://models.example.invalid/v1",
            provider=SimpleNamespace(chat=SimpleNamespace(completions=_ChatCompletionsStub())),
        )


@pytest.mark.parametrize(
    "environment_name",
    [
        "OPENAI_ADMIN_KEY",
        "OPENAI_CUSTOM_HEADERS",
        "OPENAI_LOG",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENAI_WEBHOOK_SECRET",
    ],
)
def test_real_sdk_clients_reject_ambient_openai_overrides(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
) -> None:
    monkeypatch.setenv(environment_name, "synthetic-ambient-value")

    with pytest.raises(ValueError, match=environment_name):
        GroqResponsesIntentClient(
            api_key="synthetic-groq-key",
            model="openai/gpt-oss-120b",
        )
