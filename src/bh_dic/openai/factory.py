"""Pure provider composition for the intent-routing boundary."""

from __future__ import annotations

from bh_dic.config import AppSettings
from bh_dic.openai.client import (
    GroqResponsesIntentClient,
    IntentClient,
    LlamaChatCompletionsIntentClient,
    ResponsesIntentClient,
)
from bh_dic.openai.prompts import INTENT_ROUTER_PROMPT


def build_intent_client(
    settings: AppSettings, *, developer_prompt: str = INTENT_ROUTER_PROMPT
) -> IntentClient:
    """Construct the configured SDK boundary without performing network I/O."""

    if settings.model_store or settings.openai_store:
        raise ValueError("provider storage must remain disabled")
    if settings.model_provider == "openai":
        if settings.openai_api_key is None or settings.openai_model is None:
            raise ValueError("OpenAI configuration is required")
        return ResponsesIntentClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            max_output_tokens=settings.model_max_output_tokens,
            reasoning_effort=settings.model_reasoning_effort,
            developer_prompt=developer_prompt,
        )

    if settings.model_provider == "groq":
        if settings.groq_api_key is None or not settings.groq_model:
            raise ValueError("Groq configuration is required")
        return GroqResponsesIntentClient(
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.groq_model,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            max_output_tokens=settings.model_max_output_tokens,
            reasoning_effort=settings.model_reasoning_effort,
            developer_prompt=developer_prompt,
        )

    if settings.model_provider == "llama":
        if settings.llama_model is None:
            raise ValueError("Llama configuration is required")
        return LlamaChatCompletionsIntentClient(
            api_key=(
                settings.llama_api_key.get_secret_value()
                if settings.llama_api_key is not None
                else None
            ),
            model=settings.llama_model,
            base_url=settings.llama_base_url,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            max_output_tokens=settings.model_max_output_tokens,
            reasoning_effort=settings.model_reasoning_effort,
            developer_prompt=developer_prompt,
        )

    raise ValueError("unsupported model provider")
