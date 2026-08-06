"""LLM provider configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None keeps the developer's real .env out of these assertions.
    return Settings(_env_file=None, **overrides)


def test_defaults_to_groq_for_both_roles() -> None:
    s = _settings()
    assert s.classifier_provider == "groq"
    assert s.classifier_model == "llama-3.1-8b-instant"
    assert s.extractor_provider == "groq"
    assert s.groq_base_url == "https://api.groq.com/openai/v1"


def test_roles_can_use_different_providers() -> None:
    s = _settings(
        classifier_provider="groq",
        classifier_model="llama-3.1-8b-instant",
        extractor_provider="anthropic",
        extractor_model="claude-sonnet-5",
    )
    assert s.classifier_provider == "groq"
    assert s.extractor_provider == "anthropic"


def test_base_url_only_set_for_openai_protocol_providers() -> None:
    s = _settings()
    assert s.base_url_for("groq") == "https://api.groq.com/openai/v1"
    assert s.base_url_for("xai") == "https://api.x.ai/v1"
    # Gemini and Anthropic use their own SDKs, so no override applies.
    assert s.base_url_for("gemini") is None
    assert s.base_url_for("anthropic") is None
    # OpenAI itself uses the SDK default.
    assert s.base_url_for("openai") is None


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(classifier_provider="llama")


def test_api_key_lookup_is_per_provider() -> None:
    s = _settings(groq_api_key="groq-key", gemini_api_key="gem-key")
    assert s.api_key_for("groq") == "groq-key"
    assert s.api_key_for("gemini") == "gem-key"
    assert s.api_key_for("openai") == ""


def test_missing_llm_keys_reports_only_referenced_providers() -> None:
    # Anthropic has no key, but nothing references it, so it is not reported.
    s = _settings(
        classifier_provider="groq",
        extractor_provider="groq",
        groq_api_key="groq-key",
    )
    assert s.missing_llm_keys == []

    s = _settings(classifier_provider="gemini", extractor_provider="groq")
    assert s.missing_llm_keys == ["gemini", "groq"]

    s = _settings(
        classifier_provider="gemini",
        extractor_provider="groq",
        groq_api_key="groq-key",
    )
    assert s.missing_llm_keys == ["gemini"]
