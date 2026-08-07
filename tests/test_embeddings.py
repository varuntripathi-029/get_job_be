"""Embedding provider abstraction, text construction and hashing."""

import pytest

from app.embeddings import (
    EMBEDDING_DIMENSIONS,
    TASK_DOCUMENT,
    TASK_QUERY,
    EmbeddingProvider,
    build_job_embedding_text,
    build_resume_embedding_text,
    content_hash,
    get_provider,
    normalize,
)
from app.embeddings.gemini import GeminiEmbeddingProvider
from app.embeddings.openai_provider import OpenAIEmbeddingProvider


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    """get_provider is cached per process; settings changes must not leak."""
    get_provider.cache_clear()
    yield
    get_provider.cache_clear()


# --- factory -----------------------------------------------------------------


def test_factory_returns_gemini_by_default() -> None:
    provider = get_provider()
    assert isinstance(provider, GeminiEmbeddingProvider)
    assert provider.name == "gemini"


def test_factory_is_cached() -> None:
    """Providers hold an SDK client and connection pool; rebuilding per call
    would defeat both."""
    assert get_provider() is get_provider()


def test_factory_selects_by_setting(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "openai")
    get_provider.cache_clear()
    assert isinstance(get_provider(), OpenAIEmbeddingProvider)


def test_unknown_provider_falls_back_to_gemini(monkeypatch) -> None:
    """An unrecognised value should degrade, not crash the worker on import."""
    from app.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "not-a-provider")
    get_provider.cache_clear()
    assert isinstance(get_provider(), GeminiEmbeddingProvider)


def test_providers_implement_the_interface() -> None:
    for cls in (GeminiEmbeddingProvider, OpenAIEmbeddingProvider):
        assert issubclass(cls, EmbeddingProvider)
        assert hasattr(cls, "embed_single")
        assert hasattr(cls, "embed_batch")


def test_openai_provider_is_an_explicit_stub() -> None:
    provider = OpenAIEmbeddingProvider()
    with pytest.raises(NotImplementedError):
        import asyncio

        asyncio.run(provider.embed_single("hello"))


async def test_gemini_returns_aligned_nones_without_a_key(monkeypatch) -> None:
    """Callers map results back to rows positionally, so a skipped batch must
    still return one entry per input."""
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")
    provider = GeminiEmbeddingProvider()
    assert provider.is_configured is False

    results = await provider.embed_batch(["a", "b", "c"])
    assert results == [None, None, None]
    assert await provider.embed_single("a") is None


async def test_empty_batch_is_a_no_op() -> None:
    assert await GeminiEmbeddingProvider().embed_batch([]) == []


def test_task_types_are_distinct() -> None:
    """Document and query sides of a retrieval pair embed differently."""
    assert TASK_DOCUMENT != TASK_QUERY


def test_dimensions_match_the_vector_columns() -> None:
    assert EMBEDDING_DIMENSIONS == 768


# --- normalisation and hashing -----------------------------------------------


def test_normalize_lowercases_and_collapses_whitespace() -> None:
    assert normalize("  Senior   Backend\n\tEngineer  ") == "senior backend engineer"


def test_hash_is_deterministic() -> None:
    assert content_hash("Python Engineer") == content_hash("Python Engineer")


def test_hash_ignores_case_and_whitespace() -> None:
    """Reformatting a description must not trigger a paid re-embed."""
    assert content_hash("Python  Engineer") == content_hash("python engineer")
    assert content_hash(" Python Engineer\n") == content_hash("Python Engineer")


def test_hash_differs_on_real_change() -> None:
    assert content_hash("Python Engineer") != content_hash("Python Engineers")


def test_hash_of_empty_text_is_stable() -> None:
    assert content_hash("") == content_hash("   ")


# --- text builders -----------------------------------------------------------


def test_job_text_joins_the_fields() -> None:
    text = build_job_embedding_text(
        "Senior Backend Engineer", "Platform", "engineering", "Python and Kafka."
    )
    assert text == "Senior Backend Engineer Platform engineering Python and Kafka."


def test_job_text_truncates_the_description() -> None:
    """Descriptions end in identical benefits boilerplate, which would wash out
    what distinguishes one role from another."""
    text = build_job_embedding_text("Engineer", None, None, "x" * 5000)
    assert text.count("x") == 500


def test_job_text_skips_missing_fields() -> None:
    assert build_job_embedding_text("Engineer") == "Engineer"
    assert build_job_embedding_text("Engineer", None, "engineering", None) == (
        "Engineer engineering"
    )


def test_resume_text_joins_parsed_fields() -> None:
    text = build_resume_embedding_text(
        ["Python", "React"], ["engineering"], "senior", ["Bengaluru"]
    )
    assert text == "Python React engineering senior Bengaluru"


def test_resume_text_handles_all_empty() -> None:
    assert build_resume_embedding_text(None, None, None, None) == ""
    assert build_resume_embedding_text([], [], None, []) == ""


def test_builders_are_deterministic() -> None:
    """Same inputs must always produce the same text, or the hash-based skip
    would re-embed on every run."""
    args = (["Python"], ["engineering"], "senior", ["Pune"])
    assert len({build_resume_embedding_text(*args) for _ in range(10)}) == 1


def test_text_is_capped_for_the_provider() -> None:
    from app.embeddings.text import MAX_INPUT_CHARS

    text = build_job_embedding_text("t" * 20000, None, None, None)
    assert len(text) <= MAX_INPUT_CHARS
