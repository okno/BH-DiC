"""Small Responses API boundary with no operational tool execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast

from pydantic import ValidationError

from bh_dic.openai.prompts import INTENT_ROUTER_PROMPT
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, RouteMetadata, Sensitivity
from bh_dic.openai.tools import build_openai_tools, tool_by_name


class IntentProviderError(RuntimeError):
    pass


class ProviderProtocol(Protocol):
    responses: Any


@dataclass(frozen=True, slots=True)
class RoutedIntent:
    envelope: IntentEnvelope
    metadata: RouteMetadata


def _parse_iso_date(raw: object) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise IntentProviderError("provider returned a non-string date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise IntentProviderError("provider returned an invalid ISO date") from exc


def _parse_parameters(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, str) or len(raw) > 4_000:
        raise IntentProviderError("provider returned invalid parameters_json")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentProviderError("provider returned malformed parameters_json") from exc
    if not isinstance(decoded, dict):
        raise IntentProviderError("parameters_json must encode an object")
    return decoded


def envelope_from_call(
    name: str, arguments: str, allowed_function_ids: frozenset[str]
) -> IntentEnvelope:
    if len(arguments) > 8_000:
        raise IntentProviderError("tool arguments exceed the local limit")
    try:
        payload = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise IntentProviderError("tool arguments are not valid JSON") from exc
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
        expected_ids = set(spec.function_ids) & set(allowed_function_ids)
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

    try:
        return IntentEnvelope(
            intent=intent,
            function_id=function_id,
            action_class=action_class,
            employee_id=payload.get("employee_id"),
            query=payload.get("query"),
            parameters=_parse_parameters(payload.get("parameters_json")),
            date_from=_parse_iso_date(payload.get("date_from")),
            date_to=_parse_iso_date(payload.get("date_to")),
            requires_clarification=payload.get("requires_clarification"),
            clarification_question=payload.get("clarification_question"),
            sensitivity=Sensitivity(sensitivity),
            confidence=float(confidence),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise IntentProviderError("provider output failed local validation") from exc


class ResponsesIntentClient:
    """Calls OpenAI once and returns a locally validated candidate intent."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_output_tokens: int = 1_200,
        reasoning_effort: str = "low",
        provider: ProviderProtocol | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        if not model:
            raise ValueError("OpenAI model is required")
        if provider is None:
            from openai import AsyncOpenAI

            provider = cast(
                ProviderProtocol,
                AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries),
            )
        self._provider: ProviderProtocol = provider
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort

    async def route(
        self, redacted_request: str, allowed_function_ids: frozenset[str]
    ) -> RoutedIntent:
        tools = build_openai_tools(allowed_function_ids)
        request: dict[str, Any] = {
            "model": self._model,
            "input": [
                {"role": "developer", "content": INTENT_ROUTER_PROMPT},
                {"role": "user", "content": redacted_request},
            ],
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "store": False,
            "max_output_tokens": self._max_output_tokens,
        }
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}
        try:
            response = await self._provider.responses.create(**request)
        except Exception as exc:  # provider exceptions are normalized at this boundary
            raise IntentProviderError("OpenAI intent routing failed") from exc

        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if len(calls) != 1:
            raise IntentProviderError("provider must return exactly one function call")
        call = calls[0]
        envelope = envelope_from_call(call.name, call.arguments, allowed_function_ids)
        request_id = getattr(response, "_request_id", None)
        return RoutedIntent(
            envelope=envelope,
            metadata=RouteMetadata(
                provider="openai",
                model=self._model,
                request_id=request_id,
                tool_name=call.name,
            ),
        )
