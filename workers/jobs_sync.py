"""Periodic ATS job synchronisation across every company."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.jobs.sync import sync_company_jobs
from app.scoring.engine import compute_score
from app.sources.models import Source
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _sync_all(db: AsyncSession) -> dict[str, int]:
    rows = list(
        (
            await db.execute(
                select(Source, Company)
                .join(Company, Company.id == Source.company_id)
                .where(
                    Source.source_type == "ats_api",
                    Source.status == "approved",
                    Company.is_active.is_(True),
                )
            )
        ).all()
    )

    totals = {"companies": 0, "new": 0, "updated": 0, "closed": 0, "failed": 0}
    for source, company in rows:
        try:
            result = await sync_company_jobs(db, company, source)
        except Exception as exc:  # noqa: BLE001 — one bad board must not stop the rest
            logger.warning("job sync failed for %s: %s", company.slug, exc)
            totals["failed"] += 1
            continue

        totals["companies"] += 1
        totals["new"] += result.new
        totals["updated"] += result.updated
        totals["closed"] += result.closed

        # Job counts feed career_page_update weighting, so the score is stale
        # until it is recomputed.
        if result.new or result.closed:
            await compute_score(db, company.id, commit=False)

    await db.commit()
    logger.info(
        "Synced %d companies: %d new jobs, %d updated, %d closed (%d failed)",
        totals["companies"], totals["new"], totals["updated"],
        totals["closed"], totals["failed"],
    )
    return totals


@celery_app.task(name="workers.jobs_sync.sync_all_jobs")
def sync_all_jobs() -> dict[str, int]:
    return run_async(with_session(_sync_all))
