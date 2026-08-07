"""Retention: crawl logs and expired resumes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawler.models import CrawlLog
from app.resumes.models import Resume
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _cleanup(db: AsyncSession) -> dict[str, int]:
    now = datetime.now(UTC)

    cutoff = now - timedelta(days=settings.crawl_log_retention_days)
    logs = await db.execute(delete(CrawlLog).where(CrawlLog.created_at < cutoff))

    # Resumes are PII with a promised lifetime; this is the mechanism that
    # actually honours `expires_at`, so it is not optional housekeeping.
    resumes = await db.execute(
        delete(Resume).where(Resume.expires_at.is_not(None), Resume.expires_at < now)
    )

    await db.commit()
    counts = {
        "crawl_logs_deleted": logs.rowcount or 0,
        "resumes_deleted": resumes.rowcount or 0,
    }
    logger.info(
        "cleanup: deleted %d crawl logs older than %d days and %d expired resumes",
        counts["crawl_logs_deleted"],
        settings.crawl_log_retention_days,
        counts["resumes_deleted"],
    )
    return counts


@celery_app.task(name="workers.cleanup.cleanup_old_crawl_logs")
def cleanup_old_crawl_logs() -> dict[str, int]:
    return run_async(with_session(_cleanup))
