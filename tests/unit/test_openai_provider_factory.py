from __future__ import annotations

from typing import Any, cast

import pytest

import bh_dic.openai.factory as factory_module
from bh_dic.config import AppSettings
from bh_dic.openai.client import IntentClient, PublicHrResponder
from bh_dic.openai.factory import build_intent_client, build_public_hr_responder
from bh_dic.openai.prompts import build_public_hr_prompt


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
        ("groq", "GroqChatCompletionsIntentClient", "openai/gpt-oss-120b"),
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


@pytest.mark.parametrize(
    ("provider", "constructor_name", "expected_model"),
    [
        ("openai", "ResponsesPublicHrClient", "synthetic-openai-model"),
        ("groq", "GroqChatCompletionsPublicHrClient", "openai/gpt-oss-120b"),
        ("llama", "LlamaChatCompletionsPublicHrClient", "synthetic-llama-model"),
    ],
)
def test_build_public_hr_responder_selects_provider_and_generic_tuning(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    constructor_name: str,
    expected_model: str,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = cast(PublicHrResponder, object())

    def constructor(**kwargs: Any) -> PublicHrResponder:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(factory_module, constructor_name, constructor)

    result = build_public_hr_responder(
        _settings(provider),
        developer_prompt="Prompt HR pubblico chiuso test.",
    )

    assert result is sentinel
    assert captured["model"] == expected_model
    assert captured["timeout_seconds"] == 17
    assert captured["max_retries"] == 1
    assert captured["max_output_tokens"] == 777
    assert captured["reasoning_effort"] == "medium"
    assert captured["developer_prompt"] == "Prompt HR pubblico chiuso test."
    assert "api_key" in captured
    if provider == "llama":
        assert captured["base_url"] == "http://127.0.0.1:11434/v1"


def test_build_public_hr_responder_uses_closed_profile_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = cast(PublicHrResponder, object())

    def constructor(**kwargs: Any) -> PublicHrResponder:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(factory_module, "ResponsesPublicHrClient", constructor)
    settings = _settings("openai").model_copy(
        update={"bot_language": "en", "bot_tone": "empathetic"}
    )

    assert build_public_hr_responder(settings) is sentinel
    assert captured["developer_prompt"] == build_public_hr_prompt(settings.language_profile)


def test_build_public_hr_responder_is_fail_closed_for_invalid_configuration() -> None:
    openai_missing = _settings("openai").model_copy(update={"openai_api_key": None})
    groq_missing = _settings("groq").model_copy(update={"groq_api_key": None})
    llama_missing = _settings("llama").model_copy(update={"llama_model": None})

    with pytest.raises(ValueError, match="OpenAI configuration"):
        build_public_hr_responder(openai_missing)
    with pytest.raises(ValueError, match="Groq configuration"):
        build_public_hr_responder(groq_missing)
    with pytest.raises(ValueError, match="Llama configuration"):
        build_public_hr_responder(llama_missing)

    storage_enabled = _settings("openai").model_copy(update={"openai_store": True})
    with pytest.raises(ValueError, match="storage"):
        build_public_hr_responder(storage_enabled)
    with pytest.raises(ValueError, match="developer prompt"):
        build_public_hr_responder(_settings("openai"), developer_prompt="   ")
