"""Embedding provider interface.

One seam for every vector we generate. Nothing outside this package should know
which provider is configured, which model it uses, or how batching works — call
`get_provider()` and use the ABC.

Providers never raise on a failed call. A missing embedding means "not matchable
yet", which the backfill worker retries; an exception would fail a whole crawl
batch over an enhancement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# Fixed by the Vector(768) columns on jobs.embedding and resumes.embedding.
# A provider returning a different width cannot be stored without a migration
# and a full re-embed of every row.
EMBEDDING_DIMENSIONS = 768

# Retrieval embeddings are asymmetric: a stored document and the query that
# should find it are embedded differently. Providers without the concept ignore
# these.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

Vector = list[float]


class EmbeddingProvider(ABC):
    """Turns text into vectors of `EMBEDDING_DIMENSIONS` floats."""

    name: str = "base"

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this provider has what it needs to run (usually an API key)."""

    @abstractmethod
    async def embed_single(
        self, text: str, *, task_type: str = TASK_DOCUMENT
    ) -> Vector | None:
        """Embed one text, or None if it could not be produced."""

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], *, task_type: str = TASK_DOCUMENT
    ) -> list[Vector | None]:
        """Embed many texts in as few calls as the provider allows.

        Returns a list positionally aligned with `texts`; entries that failed
        are None. Callers rely on that alignment to map results back to rows.
        """
