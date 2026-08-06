"""LLM provider configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None keeps the developer's real .env out of these assertions.
    return Settings(_env_file=None, **overrides)


def test_defaults_to_xai_for_both_roles() -> None:
    s = _settings()
    assert s.classifier_provider == "xai"
    assert s.extractor_provider == "xai"
    assert s.xai_base_url == "https://api.x.ai/v1"


def test_roles_can_use_different_providers() -> None:
    s = _settings(
        classifier_provider="gemini",
        classifier_model="gemini-2.5-flash-lite",
        extractor_provider="xai",
        extractor_model="grok-4.3",
    )
    assert s.classifier_provider == "gemini"
    assert s.extractor_provider == "xai"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(classifier_provider="llama")


def test_api_key_lookup_is_per_provider() -> None:
    s = _settings(xai_api_key="xai-key", gemini_api_key="gem-key")
    assert s.api_key_for("xai") == "xai-key"
    assert s.api_key_for("gemini") == "gem-key"
    assert s.api_key_for("openai") == ""


def test_missing_llm_keys_reports_only_referenced_providers() -> None:
    # Anthropic has no key, but nothing references it, so it is not reported.
    s = _settings(
        classifier_provider="xai",
        extractor_provider="xai",
        xai_api_key="xai-key",
    )
    assert s.missing_llm_keys == []

    s = _settings(classifier_provider="gemini", extractor_provider="xai")
    assert s.missing_llm_keys == ["gemini", "xai"]

    s = _settings(
        classifier_provider="gemini",
        extractor_provider="xai",
        xai_api_key="xai-key",
    )
    assert s.missing_llm_keys == ["gemini"]
