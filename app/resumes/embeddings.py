"""Embedding generation, shared by resumes and jobs.

Groq serves no embedding model, so this path always runs on a different provider
from the chat roles — Gemini by default, which has a free tier covering far more
than this project will generate.

Vectors are 768-dimensional to match `Vector(768)` on `jobs.embedding` and
`resumes.embedding`. A model with a different native width cannot be swapped in
without a migration and a full re-embed, so the width is asserted on every call.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Gemini distinguishes the two sides of a retrieval pair: documents are embedded
# once at write time, queries at read time. Using the same task type for both
# measurably degrades ranking.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

MAX_ATTEMPTS = 3

_gemini_client = None


def _client():
    """Lazily construct the Gemini client so an unset key is not an import error."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


def _truncate(text: str) -> str:
    return " ".join(text.split())[: settings.embedding_max_chars]


async def _embed_gemini(text: str, task_type: str) -> list[float] | None:
    from google.genai import types

    # The SDK's async surface is used directly; there is no blocking call to
    # offload here.
    response = await _client().aio.models.embed_content(
        model=settings.embedding_model,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    if not response.embeddings:
        return None
    return list(response.embeddings[0].values or [])


async def _embed_openai(text: str, _task_type: str) -> list[float] | None:
    # OpenAI has no task-type concept; symmetric embeddings for both sides.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text,
        dimensions=settings.embedding_dimensions,
    )
    return list(response.data[0].embedding)


async def generate_embedding(
    text: str, *, task_type: str = TASK_DOCUMENT
) -> list[float] | None:
    """Generate one embedding vector, or None if it could not be produced.

    Never raises. Callers treat a missing embedding as "not matchable yet" and
    retry later, which is what the periodic backfill task relies on.
    """
    if not settings.embeddings_enabled:
        logger.warning(
            "no API key for embedding provider %r — skipping embedding",
            settings.embedding_provider,
        )
        return None

    content = _truncate(text)
    if not content:
        return None

    backend = (
        _embed_gemini if settings.embedding_provider == "gemini" else _embed_openai
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            vector = await backend(content, task_type)
        except Exception as exc:  # noqa: BLE001 — SDKs raise many error types
            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "embedding failed after %d attempts: %s", MAX_ATTEMPTS, exc
                )
                return None
            await asyncio.sleep(2 ** (attempt - 1))
            continue

        if vector is None:
            logger.warning("embedding provider returned no vector")
            return None
        if len(vector) != settings.embedding_dimensions:
            # Storing this would raise at the driver anyway; failing here names
            # the real cause instead of a pgvector dimension mismatch.
            logger.error(
                "embedding model %r returned %d dims, expected %d — "
                "the vector columns cannot store it",
                settings.embedding_model,
                len(vector),
                settings.embedding_dimensions,
            )
            return None
        return vector

    return None


def resume_embedding_text(
    skills: list[str] | None,
    role_families: list[str] | None,
    seniority: str | None,
    locations: list[str] | None,
) -> str:
    """Build the text a resume is embedded from.

    Deliberately the parsed fields rather than the raw resume: raw text is
    dominated by employer names, dates and formatting boilerplate, which pull the
    vector away from what the candidate can actually do.
    """
    parts = [
        " ".join(skills or []),
        " ".join(role_families or []),
        seniority or "",
        " ".join(locations or []),
    ]
    return " ".join(part for part in parts if part).strip()


def job_embedding_text(
    title: str,
    department: str | None,
    role_family: str | None,
    description_text: str | None,
) -> str:
    """Build the text a job is embedded from.

    The description is capped well below the model limit: job descriptions end in
    boilerplate about benefits and equal-opportunity policy, which is identical
    across postings and would make every job look alike.
    """
    parts = [
        title,
        department or "",
        role_family or "",
        (description_text or "")[:500],
    ]
    return " ".join(part for part in parts if part).strip()
