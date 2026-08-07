"""OpenAI embedding provider — not implemented.

Present so the factory has a second branch and the abstraction is exercised by
more than one class. text-embedding-3-small can be requested at 768 dimensions,
so it would be a valid drop-in, but nothing here needs it yet.
"""

from __future__ import annotations

from app.config import settings
from app.embeddings.base import TASK_DOCUMENT, EmbeddingProvider, Vector


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"

    @property
    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    async def embed_single(
        self, text: str, *, task_type: str = TASK_DOCUMENT
    ) -> Vector | None:
        raise NotImplementedError(
            "OpenAI embeddings are not implemented. Set EMBEDDING_PROVIDER=gemini."
        )

    async def embed_batch(
        self, texts: list[str], *, task_type: str = TASK_DOCUMENT
    ) -> list[Vector | None]:
        raise NotImplementedError(
            "OpenAI embeddings are not implemented. Set EMBEDDING_PROVIDER=gemini."
        )
