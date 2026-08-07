"""Backfill embeddings for jobs that lack one.

Batched, not one call per job: the provider accepts up to 100 inputs per
request, so a 100-job backfill is one round trip instead of a hundred.

Two levels of deduplication keep paid calls down. Within a batch, identical
embedding text is embedded once and the vector fanned out. Across batches, a job
whose description already produced an embedding on a sibling posting copies it
without any call at all — companies routinely post the same role in ten cities.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import (
    TASK_DOCUMENT,
    build_job_embedding_text,
    content_hash,
    embeddings_available,
    get_provider,
)
from app.jobs.models import Job
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100


async def _copy_from_siblings(db: AsyncSession, jobs: list[Job]) -> int:
    """Reuse an embedding already computed for identical content.

    Keyed on jobs.content_hash, which is the SHA of the description. Title is
    compared too: two roles at one company often share a description, and
    copying across them would give a backend and a frontend posting the same
    vector.
    """
    hashes = {j.content_hash for j in jobs if j.content_hash}
    if not hashes:
        return 0

    donors = list(
        (
            await db.execute(
                select(Job).where(
                    Job.content_hash.in_(hashes), Job.embedding.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if not donors:
        return 0

    by_key = {(d.content_hash, d.title): d.embedding for d in donors}
    copied = 0
    for job in jobs:
        vector = by_key.get((job.content_hash, job.title))
        if vector is not None:
            job.embedding = vector
            copied += 1
    return copied


async def _backfill(db: AsyncSession, batch_size: int) -> dict[str, int]:
    jobs = list(
        (
            await db.execute(
                select(Job)
                .where(Job.is_active.is_(True), Job.embedding.is_(None))
                # Oldest first, so a job posted weeks ago is not starved by new
                # arrivals.
                .order_by(Job.first_seen_at)
                .limit(batch_size)
            )
        )
        .scalars()
        .all()
    )
    if not jobs:
        logger.info("no jobs awaiting embeddings")
        return {"processed": 0, "embedded": 0, "copied": 0, "failed": 0, "calls": 0}

    copied = await _copy_from_siblings(db, jobs)
    remaining = [j for j in jobs if j.embedding is None]

    # Collapse identical text to one API call, then fan the vector back out.
    by_hash: dict[str, list[Job]] = {}
    texts: dict[str, str] = {}
    skipped = 0
    for job in remaining:
        text = build_job_embedding_text(
            job.title, job.department, job.role_family, job.description_text
        )
        if not text:
            skipped += 1
            continue
        digest = content_hash(text)
        by_hash.setdefault(digest, []).append(job)
        texts.setdefault(digest, text)

    embedded = failed = 0
    if by_hash:
        digests = list(by_hash)
        vectors = await get_provider().embed_batch(
            [texts[d] for d in digests], task_type=TASK_DOCUMENT
        )
        for digest, vector in zip(digests, vectors, strict=True):
            if vector is None:
                failed += len(by_hash[digest])
                continue
            for job in by_hash[digest]:
                job.embedding = vector
                embedded += 1

    # One commit for the batch. Anything still NULL is simply picked up next run.
    await db.commit()

    result = {
        "processed": len(jobs),
        "embedded": embedded,
        "copied": copied,
        "failed": failed + skipped,
        "calls": len(by_hash),
    }
    logger.info(
        "Generated embeddings for %d jobs (%d copied from siblings, %d failed) "
        "using %d unique texts",
        embedded,
        copied,
        result["failed"],
        len(by_hash),
    )
    return result


@celery_app.task(name="workers.embeddings.generate_job_embeddings")
def generate_job_embeddings(batch_size: int = DEFAULT_BATCH_SIZE) -> dict[str, int]:
    """Embed a batch of jobs that do not yet have a vector."""
    if not embeddings_available():
        logger.warning(
            "embedding provider is not configured — skipping backfill. "
            "Crawling, scoring and search are unaffected."
        )
        return {"processed": 0, "embedded": 0, "copied": 0, "failed": 0, "calls": 0}

    return run_async(with_session(lambda db: _backfill(db, batch_size)))
