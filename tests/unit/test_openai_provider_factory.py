from __future__ import annotations

from typing import Any, cast

import pytest

import bh_dic.openai.factory as factory_module
from bh_dic.config import AppSettings
from bh_dic.openai.client import IntentClient
from bh_dic.openai.factory import build_intent_client


def _settings(provider: str) -> AppSettings:
    return AppSettings(
        app_env="mock",
        mock_mode=True,
        model_provider=provider,
        model_timeout_seconds=17,
        model_max_retries=1,
        model_max_output_tokens=777,
        model_reasoning_effort="medium",
        openai_api_key="synthetic-openai-key",
        openai_model="synthetic-openai-model",
        groq_api_key="synthetic-groq-key",
        llama_model="synthetic-llama-model",
        _env_file=None,
    )


@pytest.mark.parametrize(
    ("provider", "constructor_name", "expected_model"),
    [
        ("openai", "ResponsesIntentClient", "synthetic-openai-model"),
        ("groq", "GroqResponsesIntentClient", "openai/gpt-oss-120b"),
        ("llama", "LlamaChatCompletionsIntentClient", "synthetic-llama-model"),
    ],
)
def test_build_intent_client_selects_provider_and_generic_tuning(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    constructor_name: str,
    expected_model: str,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = cast(IntentClient, object())

    def constructor(**kwargs: Any) -> IntentClient:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(factory_module, constructor_name, constructor)

    result = build_intent_client(_settings(provider), developer_prompt="Prompt chiuso test.")

    assert result is sentinel
    assert captured["model"] == expected_model
    assert captured["timeout_seconds"] == 17
    assert captured["max_retries"] == 1
    assert captured["max_output_tokens"] == 777
    assert captured["reasoning_effort"] == "medium"
    assert captured["developer_prompt"] == "Prompt chiuso test."
    assert "api_key" in captured
    if provider == "llama":
        assert captured["base_url"] == "http://127.0.0.1:11434/v1"


def test_build_intent_client_is_fail_closed_for_missing_selected_configuration() -> None:
    openai_missing = _settings("openai").model_copy(update={"openai_api_key": None})
    groq_missing = _settings("groq").model_copy(update={"groq_api_key": None})
    llama_missing = _settings("llama").model_copy(update={"llama_model": None})

    with pytest.raises(ValueError, match="OpenAI configuration"):
        build_intent_client(openai_missing)
    with pytest.raises(ValueError, match="Groq configuration"):
        build_intent_client(groq_missing)
    with pytest.raises(ValueError, match="Llama configuration"):
        build_intent_client(llama_missing)

    storage_enabled = _settings("openai").model_copy(update={"model_store": True})
    with pytest.raises(ValueError, match="storage"):
        build_intent_client(storage_enabled)
