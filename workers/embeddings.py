"""Backfill embeddings for jobs that have none."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.jobs.models import Job
from app.resumes.embeddings import generate_embedding, job_embedding_text
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _backfill(db: AsyncSession, batch_size: int) -> dict[str, int]:
    result = await db.execute(
        select(Job)
        .where(Job.is_active.is_(True), Job.embedding.is_(None))
        # Oldest first, so a job posted weeks ago is not starved by new arrivals.
        .order_by(Job.first_seen_at)
        .limit(batch_size)
    )
    jobs = list(result.scalars().all())
    if not jobs:
        logger.info("no jobs awaiting embeddings")
        return {"processed": 0, "embedded": 0, "failed": 0}

    embedded = failed = 0
    for job in jobs:
        text = job_embedding_text(
            job.title, job.department, job.role_family, job.description_text
        )
        vector = await generate_embedding(text) if text else None
        if vector is None:
            failed += 1
            continue
        job.embedding = vector
        embedded += 1

    # One commit for the batch — a partial batch is fine, since anything still
    # NULL is simply picked up on the next run.
    await db.commit()
    logger.info(
        "Generated embeddings for %d jobs (%d failed of %d attempted)",
        embedded,
        failed,
        len(jobs),
    )
    return {"processed": len(jobs), "embedded": embedded, "failed": failed}


@celery_app.task(name="workers.embeddings.generate_job_embeddings")
def generate_job_embeddings(batch_size: int = 50) -> dict[str, int]:
    """Embed a batch of jobs that do not yet have a vector."""
    if not settings.embeddings_enabled:
        logger.warning(
            "no API key for embedding provider %r — skipping backfill",
            settings.embedding_provider,
        )
        return {"processed": 0, "embedded": 0, "failed": 0}

    return run_async(with_session(lambda db: _backfill(db, batch_size)))
