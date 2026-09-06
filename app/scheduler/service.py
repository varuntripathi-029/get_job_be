"""Run the Beat-driven jobs inline, inside an HTTP request.

Celery Beat assumes a process that runs forever. Free hosting generally offers
one process that sleeps when idle, so Beat has nowhere to live. These runners
do the same work synchronously, driven by an external cron.

Two properties make that safe rather than merely convenient:

- **Work is leased before it runs.** `_tick` pushes each due source's
  `next_crawl_at` forward before returning it, so a request that dies halfway
  through a batch loses nothing — the unreached sources come back when the
  lease expires. This is the same mechanism that stops two overlapping Beat
  ticks double-dispatching, reused.
- **Every run is bounded.** A batch limit and a wall-clock deadline keep the
  request inside the host's proxy timeout. Whatever does not fit is simply the
  next tick's work.

The Celery tasks are left in place. This is an alternative trigger, not a
replacement: a deployment with a real worker should keep using Beat, which
parallelises and retries properly.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def run_tick(
    *,
    limit: int | None = None,
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    """Crawl whatever is due, newest lease first, until the budget runs out."""
    # Imported here, not at module scope: workers.crawl builds the Celery app
    # on import, and the API process should not pay that cost on startup for
    # an endpoint most deployments never call.
    from workers.crawl import _crawl, _tick

    limit = limit or settings.scheduler_batch_size
    deadline = deadline_seconds or settings.scheduler_deadline_seconds
    started = time.monotonic()

    async with AsyncSessionLocal() as db:
        due = await _tick(db)

    if not due:
        return {"due": 0, "crawled": 0, "deferred": 0, "results": []}

    results: list[dict[str, Any]] = []
    crawled = 0

    for source_id in due[:limit]:
        elapsed = time.monotonic() - started
        if elapsed >= deadline:
            logger.info(
                "scheduler tick hit its %.0fs deadline after %d sources",
                deadline,
                crawled,
            )
            break

        # A fresh session per source, matching how the Celery task runs. One
        # source failing must not poison the transaction of the next.
        async with AsyncSessionLocal() as db:
            try:
                outcome = await _crawl(db, source_id)
                results.append(outcome)
                crawled += 1
            except Exception as exc:  # noqa: BLE001 — one bad source, not a bad tick
                await db.rollback()
                logger.warning("scheduler crawl failed for %s: %s", source_id, exc)
                results.append({"source_id": source_id, "error": str(exc)[:200]})

    return {
        "due": len(due),
        "crawled": crawled,
        # Leased but not reached this run. They return when the lease lapses.
        "deferred": max(0, len(due) - crawled),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "results": results,
    }


async def run_retier_sweep() -> dict[str, Any]:
    """Promote any source now recognisable as a parseable ATS to the ats_api
    tier. Cheap and idempotent — a no-op once everything is on the right tier —
    so an external cron can call it as often as it likes."""
    from app.sources.service import resync_fetch_tiers

    async with AsyncSessionLocal() as db:
        return await resync_fetch_tiers(db)


async def run_sync_jobs() -> dict[str, Any]:
    from workers.jobs_sync import _sync_all

    async with AsyncSessionLocal() as db:
        return await _sync_all(db)


async def run_embeddings(batch_size: int | None = None) -> dict[str, Any]:
    from workers.embeddings import DEFAULT_BATCH_SIZE, _backfill

    async with AsyncSessionLocal() as db:
        return await _backfill(db, batch_size or DEFAULT_BATCH_SIZE)


async def run_cleanup() -> dict[str, Any]:
    from workers.cleanup import _cleanup

    async with AsyncSessionLocal() as db:
        return await _cleanup(db)


async def run_newsletter() -> dict[str, Any]:
    from workers.newsletter import _run

    async with AsyncSessionLocal() as db:
        return await _run(db)
