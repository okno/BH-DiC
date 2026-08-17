"""Small Responses API boundary with no operational tool execution."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol, cast

from openai import BadRequestError
from pydantic import ValidationError

from bh_dic.openai.prompts import INTENT_ROUTER_PROMPT
from bh_dic.openai.providers import (
    GROQ_OPENAI_BASE_URL,
    OPENAI_RESPONSES_BASE_URL,
    llama_endpoint_is_loopback,
    validate_llama_base_url,
)
from bh_dic.openai.schemas import (
    ActionClass,
    IntentEnvelope,
    ProviderTokenUsage,
    RouteMetadata,
    Sensitivity,
)
from bh_dic.openai.tools import build_openai_tools, tool_by_name
from bh_dic.policies.catalog import (
    FUNCTION_CATALOG,
    WriteParameterValidationError,
    validate_write_parameters,
)


class ProviderFailureKind(StrEnum):
    """Closed, non-sensitive classification of a failed provider request."""

    UNCLASSIFIED = "UNCLASSIFIED"
    TOOL_USE_FAILED = "TOOL_USE_FAILED"


class IntentProviderError(RuntimeError):
    """Sanitized provider failure with optional completed-response telemetry.

    ``response_received`` distinguishes an unavailable counter on a completed provider response
    from a request whose remote outcome is unknown.  The exception deliberately carries neither
    provider bodies nor request IDs.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        usage: ProviderTokenUsage | None = None,
        response_received: bool = False,
        failure_kind: ProviderFailureKind = ProviderFailureKind.UNCLASSIFIED,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.usage = usage
        self.response_received = response_received
        self.failure_kind = failure_kind


_FORBIDDEN_SDK_ENVIRONMENT = (
    "OPENAI_ADMIN_KEY",
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_LOG",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_WEBHOOK_SECRET",
)


class ResponsesProviderProtocol(Protocol):
    responses: Any


class ChatCompletionsProviderProtocol(Protocol):
    chat: Any


@dataclass(frozen=True, slots=True)
class RoutedIntent:
    envelope: IntentEnvelope
    metadata: RouteMetadata


class IntentClient(Protocol):
    async def route(
        self, redacted_request: str, allowed_function_ids: frozenset[str]
    ) -> RoutedIntent: ...


def _validated_developer_prompt(value: str) -> str:
    prompt = value.strip()
    if not prompt:
        raise ValueError("developer prompt is required")
    if len(prompt) > 20_000:
        raise ValueError("developer prompt exceeds the local limit")
    return prompt


def _no_redirect_http_client() -> Any:
    """Build the SDK transport without forwarding prompts across redirects."""

    from openai import DefaultAsyncHttpxClient

    return DefaultAsyncHttpxClient(follow_redirects=False, trust_env=False)


def _reject_ambient_sdk_overrides() -> None:
    configured = sorted(name for name in _FORBIDDEN_SDK_ENVIRONMENT if os.environ.get(name))
    if configured:
        names = ", ".join(configured)
        raise ValueError(f"ambient OpenAI SDK overrides are forbidden: {names}")


def _parse_iso_date(raw: object) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise IntentProviderError("provider returned a non-string date")
    parsed: date | None = None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        pass
    if parsed is None:
        raise IntentProviderError("provider returned an invalid ISO date")
    return parsed


def _parse_parameters(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, str) or len(raw) > 4_000:
        raise IntentProviderError("provider returned invalid parameters_json")
    decoded: object | None = None
    malformed = False
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        malformed = True
    if malformed:
        raise IntentProviderError("provider returned malformed parameters_json")
    if not isinstance(decoded, dict):
        raise IntentProviderError("parameters_json must encode an object")
    return decoded


_MISSING = object()


def _usage_member(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name, _MISSING)
    return getattr(value, name, _MISSING)


def _provider_token_usage(
    response: object,
    *,
    input_field: str,
    output_field: str,
) -> ProviderTokenUsage | None:
    """Normalize exact provider counters without coercion or local estimation."""

    raw_usage: object = _MISSING
    read_failed = False
    try:
        raw_usage = _usage_member(response, "usage")
    except Exception:
        read_failed = True
    if read_failed:
        raise IntentProviderError("provider token usage could not be read")
    if raw_usage is _MISSING or raw_usage is None:
        return None
    values: dict[str, object] | None = None
    read_failed = False
    try:
        values = {
            "input_tokens": _usage_member(raw_usage, input_field),
            "output_tokens": _usage_member(raw_usage, output_field),
            "total_tokens": _usage_member(raw_usage, "total_tokens"),
        }
    except Exception:
        read_failed = True
    if read_failed or values is None:
        raise IntentProviderError("provider token usage could not be read")
    if any(value is _MISSING for value in values.values()):
        raise IntentProviderError("provider returned incomplete token usage")
    usage: ProviderTokenUsage | None = None
    invalid_usage = False
    try:
        usage = ProviderTokenUsage.model_validate(values)
    except ValidationError:
        invalid_usage = True
    if invalid_usage or usage is None:
        raise IntentProviderError("provider returned invalid token usage")
    return usage


def _response_error_with_context(
    error: IntentProviderError,
    *,
    provider: str,
    model: str,
    usage: ProviderTokenUsage | None,
) -> IntentProviderError:
    """Attach safe telemetry while suppressing the original exception chain."""

    return IntentProviderError(
        str(error),
        provider=provider,
        model=model,
        usage=usage,
        response_received=True,
        failure_kind=error.failure_kind,
    )


def _classify_provider_failure(
    error: Exception,
    *,
    provider: str,
) -> ProviderFailureKind:
    """Classify only bounded SDK metadata, never a provider body or message."""

    if provider != "groq" or type(error) is not BadRequestError:
        return ProviderFailureKind.UNCLASSIFIED
    status_code: object | None = None
    code: object | None = None
    metadata_failed = False
    try:
        status_code = error.status_code
        code = error.code
    except Exception:
        metadata_failed = True
    if (
        not metadata_failed
        and type(status_code) is int
        and status_code == 400
        and type(code) is str
        and code == "tool_use_failed"
    ):
        return ProviderFailureKind.TOOL_USE_FAILED
    return ProviderFailureKind.UNCLASSIFIED


def envelope_from_call(
    name: str, arguments: str, allowed_function_ids: frozenset[str]
) -> IntentEnvelope:
    if len(arguments) > 8_000:
        raise IntentProviderError("tool arguments exceed the local limit")
    payload: object | None = None
    malformed = False
    try:
        payload = json.loads(arguments)
    except json.JSONDecodeError:
        malformed = True
    if malformed:
        raise IntentProviderError("tool arguments are not valid JSON")
    if not isinstance(payload, dict):
        raise IntentProviderError("tool arguments must be an object")

    if name == "unsupported_request":
        expected_ids = {"UNSUPPORTED"}
        action_class = ActionClass.UNSUPPORTED
        intent = "unsupported"
    else:
        spec = tool_by_name(name)
        if spec is None:
            raise IntentProviderError("provider selected an unknown tool")
        expected_ids = {
            function_id
            for function_id in set(spec.function_ids) & set(allowed_function_ids)
            if FUNCTION_CATALOG[function_id].expose_to_model
        }
        action_class = spec.action_class
        intent = spec.intent

    function_id = payload.get("function_id")
    if function_id not in expected_ids:
        raise IntentProviderError("provider selected a non-exposed function_id")

    sensitivity = payload.get("sensitivity")
    confidence = payload.get("confidence")
    if not isinstance(sensitivity, str):
        raise IntentProviderError("provider returned an invalid sensitivity")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise IntentProviderError("provider returned an invalid confidence")

    parameters = _parse_parameters(payload.get("parameters_json"))
    function_spec = FUNCTION_CATALOG.get(str(function_id))
    if function_spec is not None and function_spec.is_write:
        invalid_write_parameters = False
        try:
            parameters = validate_write_parameters(function_spec, parameters)
        except WriteParameterValidationError:
            invalid_write_parameters = True
        if invalid_write_parameters:
            raise IntentProviderError("provider write parameters violate the local catalog")

    envelope: IntentEnvelope | None = None
    invalid_envelope = False
    try:
        envelope = IntentEnvelope(
            intent=intent,
            function_id=function_id,
            action_class=action_class,
            employee_id=payload.get("employee_id"),
            query=payload.get("query"),
            parameters=parameters,
            date_from=_parse_iso_date(payload.get("date_from")),
            date_to=_parse_iso_date(payload.get("date_to")),
            requires_clarification=payload.get("requires_clarification"),
            clarification_question=payload.get("clarification_question"),
            sensitivity=Sensitivity(sensitivity),
            confidence=float(confidence),
        )
    except (TypeError, ValueError, ValidationError):
        invalid_envelope = True
    if invalid_envelope or envelope is None:
        raise IntentProviderError("provider output failed local validation")
    return envelope


class ResponsesIntentClient:
    """Calls a pinned Responses API once and locally validates its candidate intent."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_output_tokens: int = 1_200,
        reasoning_effort: str = "low",
        developer_prompt: str = INTENT_ROUTER_PROMPT,
        provider: ResponsesProviderProtocol | None = None,
        provider_name: str = "openai",
    ) -> None:
        if not api_key:
            raise ValueError("Provider API key is required")
        if not model:
            raise ValueError("Provider model is required")
        if provider_name not in {"openai", "groq"}:
            raise ValueError("unsupported Responses API provider")
        if provider is None:
            _reject_ambient_sdk_overrides()
            from openai import AsyncOpenAI

            provider = cast(
                ResponsesProviderProtocol,
                AsyncOpenAI(
                    api_key=api_key,
                    base_url=OPENAI_RESPONSES_BASE_URL,
                    timeout=timeout_seconds,
                    max_retries=max_retries,
                    http_client=_no_redirect_http_client(),
                ),
            )
        self._provider: ResponsesProviderProtocol = provider
        self._provider_name = provider_name
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._developer_prompt = _validated_developer_prompt(developer_prompt)

    async def route(
        self, redacted_request: str, allowed_function_ids: frozenset[str]
    ) -> RoutedIntent:
        tools = build_openai_tools(allowed_function_ids)
        request: dict[str, Any] = {
            "model": self._model,
            "input": [
                {"role": "developer", "content": self._developer_prompt},
                {"role": "user", "content": redacted_request},
            ],
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "store": False,
            "max_output_tokens": self._max_output_tokens,
        }
        if self._reasoning_effort and self._reasoning_effort != "none":
            request["reasoning"] = {"effort": self._reasoning_effort}
        response: Any = _MISSING
        provider_failure_kind = ProviderFailureKind.UNCLASSIFIED
        try:
            response = await self._provider.responses.create(**request)
        except Exception as exc:  # provider exceptions and response bodies are private
            provider_failure_kind = _classify_provider_failure(
                exc,
                provider=self._provider_name,
            )
        if response is _MISSING:
            raise IntentProviderError(
                f"{self._provider_name} intent routing failed",
                provider=self._provider_name,
                model=self._model,
                response_received=(provider_failure_kind is ProviderFailureKind.TOOL_USE_FAILED),
                failure_kind=provider_failure_kind,
            ) from None

        usage: ProviderTokenUsage | None = None
        usage_failure: IntentProviderError | None = None
        try:
            usage = _provider_token_usage(
                response,
                input_field="input_tokens",
                output_field="output_tokens",
            )
        except IntentProviderError as exc:
            usage_failure = _response_error_with_context(
                exc,
                provider=self._provider_name,
                model=self._model,
                usage=None,
            )
        if usage_failure is not None:
            raise usage_failure

        call: Any | None = None
        envelope: IntentEnvelope | None = None
        request_id: object | None = None
        output_failure: IntentProviderError | None = None
        try:
            calls = [
                item for item in response.output if getattr(item, "type", None) == "function_call"
            ]
            if len(calls) != 1:
                raise IntentProviderError("provider must return exactly one function call")
            call = calls[0]
            envelope = envelope_from_call(call.name, call.arguments, allowed_function_ids)
            request_id = getattr(response, "_request_id", None)
        except IntentProviderError as exc:
            output_failure = _response_error_with_context(
                exc,
                provider=self._provider_name,
                model=self._model,
                usage=usage,
            )
        except Exception:
            output_failure = IntentProviderError(
                "provider output validation failed",
                provider=self._provider_name,
                model=self._model,
                usage=usage,
                response_received=True,
            )
        if output_failure is not None:
            raise output_failure
        if call is None or envelope is None:
            raise IntentProviderError(
                "provider output validation failed",
                provider=self._provider_name,
                model=self._model,
                usage=usage,
                response_received=True,
            )
        metadata: RouteMetadata | None = None
        metadata_failed = False
        try:
            metadata = RouteMetadata(
                provider=self._provider_name,
                model=self._model,
                request_id=request_id,
                tool_name=call.name,
                usage=usage,
            )
        except (TypeError, ValueError, ValidationError):
            metadata_failed = True
        if metadata_failed or metadata is None:
            raise IntentProviderError(
                "provider metadata validation failed",
                provider=self._provider_name,
                model=self._model,
                usage=usage,
                response_received=True,
            )
        return RoutedIntent(envelope=envelope, metadata=metadata)


class GroqResponsesIntentClient(ResponsesIntentClient):
    """Responses API client pinned to Groq's official OpenAI-compatible origin."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_output_tokens: int = 1_200,
        reasoning_effort: str = "low",
        developer_prompt: str = INTENT_ROUTER_PROMPT,
        provider: ResponsesProviderProtocol | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        if not model:
            raise ValueError("Groq model is required")
        if provider is None:
            _reject_ambient_sdk_overrides()
            from openai import AsyncOpenAI

            provider = cast(
                ResponsesProviderProtocol,
                AsyncOpenAI(
                    api_key=api_key,
                    base_url=GROQ_OPENAI_BASE_URL,
                    timeout=timeout_seconds,
                    max_retries=max_retries,
                    http_client=_no_redirect_http_client(),
                ),
            )
        super().__init__(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            developer_prompt=developer_prompt,
            provider=provider,
            provider_name="groq",
        )


def _chat_completions_tools(allowed_function_ids: frozenset[str]) -> list[dict[str, Any]]:
    """Convert the closed Responses tool catalog to Chat Completions format."""

    result: list[dict[str, Any]] = []
    for tool in build_openai_tools(allowed_function_ids):
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                    "strict": tool["strict"],
                },
            }
        )
    return result


class LlamaChatCompletionsIntentClient:
    """Tool-calling client for a validated OpenAI-compatible llama endpoint."""

    _NO_KEY_SENTINEL = "local-endpoint-no-api-key"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_output_tokens: int = 1_200,
        reasoning_effort: str = "low",
        developer_prompt: str = INTENT_ROUTER_PROMPT,
        provider: ChatCompletionsProviderProtocol | None = None,
    ) -> None:
        if not model:
            raise ValueError("Llama model is required")
        self._base_url = validate_llama_base_url(base_url)
        if not llama_endpoint_is_loopback(self._base_url) and not api_key:
            raise ValueError("Llama API key is required for a remote endpoint")
        if provider is None:
            _reject_ambient_sdk_overrides()
            from openai import AsyncOpenAI

            provider = cast(
                ChatCompletionsProviderProtocol,
                AsyncOpenAI(
                    api_key=api_key or self._NO_KEY_SENTINEL,
                    base_url=self._base_url,
                    timeout=timeout_seconds,
                    max_retries=max_retries,
                    http_client=_no_redirect_http_client(),
                ),
            )
        self._provider: ChatCompletionsProviderProtocol = provider
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._developer_prompt = _validated_developer_prompt(developer_prompt)
        # OpenAI-compatible local servers do not agree on a reasoning parameter.
        # Accept the generic setting at the composition boundary but deliberately
        # omit it from this interoperable Chat Completions request.
        del reasoning_effort

    async def route(
        self, redacted_request: str, allowed_function_ids: frozenset[str]
    ) -> RoutedIntent:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._developer_prompt},
                {"role": "user", "content": redacted_request},
            ],
            "tools": _chat_completions_tools(allowed_function_ids),
            "tool_choice": "required",
            "n": 1,
            "max_tokens": self._max_output_tokens,
        }
        response: object = _MISSING
        provider_failure_kind = ProviderFailureKind.UNCLASSIFIED
        try:
            response = await self._provider.chat.completions.create(**request)
        except Exception as exc:  # provider exceptions and response bodies are private
            provider_failure_kind = _classify_provider_failure(exc, provider="llama")
        if response is _MISSING:
            raise IntentProviderError(
                "llama intent routing failed",
                provider="llama",
                model=self._model,
                response_received=False,
                failure_kind=provider_failure_kind,
            ) from None

        usage: ProviderTokenUsage | None = None
        usage_failure: IntentProviderError | None = None
        try:
            usage = _provider_token_usage(
                response,
                input_field="prompt_tokens",
                output_field="completion_tokens",
            )
        except IntentProviderError as exc:
            usage_failure = _response_error_with_context(
                exc,
                provider="llama",
                model=self._model,
                usage=None,
            )
        if usage_failure is not None:
            raise usage_failure

        name: str | None = None
        arguments: str | None = None
        envelope: IntentEnvelope | None = None
        request_id: object | None = None
        output_failure: IntentProviderError | None = None
        try:
            choices = getattr(response, "choices", None)
            if not isinstance(choices, list) or len(choices) != 1:
                raise IntentProviderError("provider must return exactly one completion choice")
            message = getattr(choices[0], "message", None)
            calls = getattr(message, "tool_calls", None)
            if not isinstance(calls, list) or len(calls) != 1:
                raise IntentProviderError("provider must return exactly one tool call")
            call = calls[0]
            if getattr(call, "type", None) != "function":
                raise IntentProviderError("provider returned a non-function tool call")
            function = getattr(call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise IntentProviderError("provider returned an invalid function call")

            envelope = envelope_from_call(name, arguments, allowed_function_ids)
            request_id = getattr(response, "_request_id", None)
        except IntentProviderError as exc:
            output_failure = _response_error_with_context(
                exc,
                provider="llama",
                model=self._model,
                usage=usage,
            )
        except Exception:
            output_failure = IntentProviderError(
                "provider output validation failed",
                provider="llama",
                model=self._model,
                usage=usage,
                response_received=True,
            )
        if output_failure is not None:
            raise output_failure
        if name is None or envelope is None:
            raise IntentProviderError(
                "provider output validation failed",
                provider="llama",
                model=self._model,
                usage=usage,
                response_received=True,
            )
        metadata: RouteMetadata | None = None
        metadata_failed = False
        try:
            metadata = RouteMetadata(
                provider="llama",
                model=self._model,
                request_id=request_id,
                tool_name=name,
                usage=usage,
            )
        except (TypeError, ValueError, ValidationError):
            metadata_failed = True
        if metadata_failed or metadata is None:
            raise IntentProviderError(
                "provider metadata validation failed",
                provider="llama",
                model=self._model,
                usage=usage,
                response_received=True,
            )
        return RoutedIntent(envelope=envelope, metadata=metadata)
