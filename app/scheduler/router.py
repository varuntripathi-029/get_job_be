"""Scheduler routes — an external cron's stand-in for Celery Beat.

Authenticated with a shared secret rather than a user JWT: the caller is a
GitHub Actions job, which cannot complete a Google sign-in. The secret buys
exactly one thing — that a stranger cannot spend your LLM and API budget by
curling these in a loop.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.common.exceptions import AppError, AuthenticationError
from app.config import settings
from app.scheduler import service

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class SchedulerDisabledError(AppError):
    status_code = 503
    error_code = "SCHEDULER_DISABLED"


async def require_scheduler_token(
    x_scheduler_token: Annotated[str | None, Header()] = None,
) -> None:
    """Gate every scheduler route on the shared secret.

    Unset means disabled, not open. A deployment that forgets to configure the
    token gets 503 on every call, which is loud and safe; the alternative —
    treating an empty secret as "no auth required" — would leave a public
    endpoint that burns quota on demand.
    """
    if not settings.scheduler_token:
        raise SchedulerDisabledError(
            "SCHEDULER_TOKEN is not configured; scheduler endpoints are disabled."
        )
    # compare_digest, not ==: string comparison short-circuits on the first
    # differing byte and leaks the prefix to anyone timing the responses.
    if not x_scheduler_token or not hmac.compare_digest(
        x_scheduler_token, settings.scheduler_token
    ):
        raise AuthenticationError("Invalid or missing X-Scheduler-Token.")


Guarded = [Depends(require_scheduler_token)]


@router.post(
    "/tick",
    dependencies=Guarded,
    summary="Crawl whatever sources are due",
)
async def tick(
    limit: Annotated[int | None, Query(ge=1, le=25)] = None,
    deadline_seconds: Annotated[float | None, Query(ge=5, le=280)] = None,
) -> dict[str, object]:
    """The replacement for Beat's 5-minute tick.

    Returns once the batch is done or the deadline is reached. Sources that
    were leased but not reached come back on the next call, so calling this
    more often simply drains the queue faster.
    """
    return await service.run_tick(limit=limit, deadline_seconds=deadline_seconds)


@router.post("/sync-jobs", dependencies=Guarded, summary="Resync every ATS board")
async def sync_jobs() -> dict[str, object]:
    return await service.run_sync_jobs()


@router.post(
    "/retier",
    dependencies=Guarded,
    summary="Promote sources now recognisable as a parseable ATS",
)
async def retier() -> dict[str, object]:
    """Keeps fetch tiers in step with the adapters we ship.

    A source's tier is frozen at creation, so any source added before its ATS
    provider was supported stays on the wrong tier. Running this on a cron means
    that self-corrects instead of waiting for someone to notice a company shows
    nothing and re-detect it by hand.
    """
    return await service.run_retier_sweep()


@router.post("/embeddings", dependencies=Guarded, summary="Backfill job embeddings")
async def embeddings(
    batch_size: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> dict[str, object]:
    return await service.run_embeddings(batch_size)


@router.post("/cleanup", dependencies=Guarded, summary="Drop old crawl logs")
async def cleanup() -> dict[str, object]:
    return await service.run_cleanup()


@router.post("/newsletter", dependencies=Guarded, summary="Send the weekly edition")
async def newsletter() -> dict[str, object]:
    return await service.run_newsletter()
