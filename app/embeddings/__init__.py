"""Embedding generation.

Public surface: `get_provider()` plus the text builders. Nothing outside this
package should import a concrete provider or know which one is configured.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.embeddings.base import (
    EMBEDDING_DIMENSIONS,
    TASK_DOCUMENT,
    TASK_QUERY,
    EmbeddingProvider,
    Vector,
)
from app.embeddings.gemini import GeminiEmbeddingProvider
from app.embeddings.openai_provider import OpenAIEmbeddingProvider
from app.embeddings.text import (
    build_job_embedding_text,
    build_resume_embedding_text,
    content_hash,
    normalize,
    truncate,
)

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "gemini": GeminiEmbeddingProvider,
    "openai": OpenAIEmbeddingProvider,
}


@lru_cache(maxsize=1)
def get_provider() -> EmbeddingProvider:
    """The configured provider, built once per process.

    Cached because providers hold an SDK client and a connection pool; a new
    one per call would defeat both. Call `get_provider.cache_clear()` after
    changing settings in a test.
    """
    name = (settings.embedding_provider or "gemini").strip().lower()
    provider_cls = _PROVIDERS.get(name)

    if provider_cls is None:
        logger.error(
            "unknown EMBEDDING_PROVIDER %r; falling back to gemini. Known: %s",
            name,
            ", ".join(sorted(_PROVIDERS)),
        )
        provider_cls = GeminiEmbeddingProvider

    return provider_cls()


def embeddings_available() -> bool:
    """Whether embeddings can actually be generated right now."""
    return get_provider().is_configured


__all__ = [
    "EMBEDDING_DIMENSIONS",
    "TASK_DOCUMENT",
    "TASK_QUERY",
    "EmbeddingProvider",
    "Vector",
    "build_job_embedding_text",
    "build_resume_embedding_text",
    "content_hash",
    "embeddings_available",
    "get_provider",
    "normalize",
    "truncate",
]
