"""Score recomputation as a standalone task."""

from __future__ import annotations

import logging
import uuid

from app.scoring.engine import compute_score
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="workers.score.recompute_score")
def recompute_score(company_id: str) -> dict[str, object]:
    """Rescore one company. Crawl tasks score inline; this is for backfills."""

    async def run(db):
        score = await compute_score(db, uuid.UUID(company_id))
        return {
            "company_id": company_id,
            "momentum_score": score.momentum_score,
            "momentum_label": score.momentum_label,
        }

    return run_async(with_session(run))
