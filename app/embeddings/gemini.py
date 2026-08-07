"""Gemini embedding provider.

Uses google-genai, not google-generativeai: the latter reached end of support
and prints a deprecation warning on import. Same models, current SDK.

text-embedding-004 is natively 768-dimensional, which is what makes it a
drop-in for the Vector(768) columns.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.embeddings.base import (
    EMBEDDING_DIMENSIONS,
    TASK_DOCUMENT,
    EmbeddingProvider,
    Vector,
)
from app.embeddings.text import truncate

logger = logging.getLogger(__name__)

# Gemini accepts up to 100 inputs per embed_content call.
MAX_BATCH = 100
MAX_ATTEMPTS = 3


class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"

    def __init__(self) -> None:
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.gemini_api_key)

    def _get_client(self):
        """Built lazily so an unset key is not an import-time error."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    async def embed_single(
        self, text: str, *, task_type: str = TASK_DOCUMENT
    ) -> Vector | None:
        results = await self.embed_batch([text], task_type=task_type)
        return results[0] if results else None

    async def embed_batch(
        self, texts: list[str], *, task_type: str = TASK_DOCUMENT
    ) -> list[Vector | None]:
        if not texts:
            return []

        if not self.is_configured:
            logger.warning(
                "GEMINI_API_KEY is unset — skipping %d embeddings", len(texts)
            )
            return [None] * len(texts)

        out: list[Vector | None] = []
        for start in range(0, len(texts), MAX_BATCH):
            chunk = texts[start : start + MAX_BATCH]
            out.extend(await self._embed_chunk(chunk, task_type))
        return out

    async def _embed_chunk(
        self, chunk: list[str], task_type: str
    ) -> list[Vector | None]:
        from google.genai import types

        prepared = [truncate(t) for t in chunk]
        config = types.EmbedContentConfig(task_type=task_type)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._get_client().aio.models.embed_content(
                    model=settings.embedding_model,
                    contents=prepared,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 — SDKs raise many types
                if attempt == MAX_ATTEMPTS:
                    logger.error(
                        "gemini embedding failed after %d attempts for %d texts: %s",
                        MAX_ATTEMPTS,
                        len(chunk),
                        exc,
                    )
                    return [None] * len(chunk)
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            embeddings = response.embeddings or []
            if len(embeddings) != len(chunk):
                # Positional alignment is the contract callers depend on to map
                # vectors back to rows; a short response cannot be trusted.
                logger.error(
                    "gemini returned %d embeddings for %d inputs — discarding batch",
                    len(embeddings),
                    len(chunk),
                )
                return [None] * len(chunk)

            return [self._validate(e.values) for e in embeddings]

        return [None] * len(chunk)

    @staticmethod
    def _validate(values) -> Vector | None:
        if not values:
            return None
        vector = list(values)
        if len(vector) != EMBEDDING_DIMENSIONS:
            # Storing this would fail at the driver with an opaque pgvector
            # error; naming the real cause here is more useful.
            logger.error(
                "embedding model %r returned %d dims, expected %d",
                settings.embedding_model,
                len(vector),
                EMBEDDING_DIMENSIONS,
            )
            return None
        return vector
