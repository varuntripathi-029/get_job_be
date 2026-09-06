"""Admin routes — source moderation, crawler health, metrics."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service as admin_service
from app.auth.dependencies import AdminUser
from app.common.exceptions import ValidationError
from app.common.pagination import PaginatedResponse, PaginationParams, get_pagination
from app.config import settings
from app.database import get_db
from app.newsletter import sender, tokens
from app.newsletter import service as newsletter_service
from app.newsletter.generator import generate_newsletter
from app.newsletter.schemas import SendResult, SubscriberAdminResponse
from app.newsletter.templates import render_newsletter_html
from app.sources import service as source_service
from app.sources.schemas import (
    CrawlerHealthRow,
    SourceAdminCreate,
    SourceReject,
    SourceResponse,
    SourceUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get("/sources/pending", response_model=PaginatedResponse[SourceResponse])
async def pending_sources(
    db: DbSession, params: Pagination, _admin: AdminUser
) -> PaginatedResponse[SourceResponse]:
    sources, total = await source_service.list_sources(db, params, status="pending")
    return PaginatedResponse.build(
        [SourceResponse.model_validate(s) for s in sources], total, params
    )


@router.post("/sources/{source_id}/approve", response_model=SourceResponse)
async def approve_source(
    source_id: uuid.UUID, db: DbSession, admin: AdminUser
) -> SourceResponse:
    """Approve and schedule for immediate crawl. Re-runs SSRF validation."""
    source = await source_service.approve_source(db, admin.id, source_id)
    return SourceResponse.model_validate(source)


@router.post("/sources/{source_id}/reject", response_model=SourceResponse)
async def reject_source(
    source_id: uuid.UUID, data: SourceReject, db: DbSession, admin: AdminUser
) -> SourceResponse:
    source = await source_service.reject_source(db, admin.id, source_id, data.reason)
    return SourceResponse.model_validate(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID, data: SourceUpdate, db: DbSession, _admin: AdminUser
) -> SourceResponse:
    source = await source_service.update_source(db, source_id, data)
    return SourceResponse.model_validate(source)


@router.post("/sources/{source_id}/disable", response_model=SourceResponse)
async def disable_source(
    source_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> SourceResponse:
    """Take an approved source out of the crawl rotation without deleting it.

    Re-approve it later with the approve endpoint to resume crawling.
    """
    source = await source_service.disable_source(db, source_id)
    return SourceResponse.model_validate(source)


@router.post("/sources/{source_id}/redetect-tier", response_model=SourceResponse)
async def redetect_tier(
    source_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> SourceResponse:
    """Recompute the fetch tier and reschedule an approved source for now."""
    source = await source_service.redetect_fetch_tier(db, source_id)
    return SourceResponse.model_validate(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> None:
    """Delete a source permanently, freeing its URL for resubmission."""
    await source_service.delete_source(db, source_id)


@router.post(
    "/companies/{company_id}/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_company_source(
    company_id: uuid.UUID,
    data: SourceAdminCreate,
    db: DbSession,
    _admin: AdminUser,
) -> SourceResponse:
    """Attach a source directly to a company, bypassing the pending queue."""
    data.company_id = company_id
    source = await source_service.create_source_as_admin(db, data)
    return SourceResponse.model_validate(source)


@router.get("/crawler/health", response_model=list[CrawlerHealthRow])
async def crawler_health(
    db: DbSession,
    _admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    only_failing: Annotated[bool, Query()] = False,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[CrawlerHealthRow]:
    return await admin_service.crawler_health(
        db, limit=limit, only_failing=only_failing, search=search
    )


@router.get("/metrics")
async def instance_metrics(db: DbSession, _admin: AdminUser) -> dict[str, object]:
    return await admin_service.instance_metrics(db)


# --- Newsletter --------------------------------------------------------------


@router.get(
    "/newsletter/subscribers",
    response_model=PaginatedResponse[SubscriberAdminResponse],
)
async def list_subscribers(
    db: DbSession,
    params: Pagination,
    _admin: AdminUser,
    is_active: Annotated[bool | None, Query()] = None,
) -> PaginatedResponse[SubscriberAdminResponse]:
    subscribers, total = await newsletter_service.list_subscribers(
        db, params, is_active=is_active
    )
    return PaginatedResponse.build(
        [SubscriberAdminResponse.model_validate(s) for s in subscribers], total, params
    )


@router.get("/newsletter/preview", response_class=HTMLResponse)
async def preview_newsletter(db: DbSession, _admin: AdminUser) -> HTMLResponse:
    """Render this week's newsletter without sending or consuming an edition
    number. The unsubscribe link points at a placeholder id, so it renders but
    does not resolve to a real subscriber."""
    content = await generate_newsletter(
        db, edition_number=await sender.current_edition_number()
    )
    html = render_newsletter_html(
        content, tokens.unsubscribe_url(uuid.UUID(int=0)), settings.frontend_url
    )
    return HTMLResponse(content=html)


@router.post("/newsletter/send-now", response_model=SendResult)
async def send_newsletter_now(
    db: DbSession,
    _admin: AdminUser,
    force: Annotated[bool, Query(description="Send even with no content.")] = False,
) -> SendResult:
    """Send immediately, bypassing the Monday schedule."""
    edition = await sender.next_edition_number()
    content = await generate_newsletter(db, edition_number=edition)

    if content.is_empty and not force:
        raise ValidationError(
            "No movers, hotspots or events in the last 7 days. "
            "Pass ?force=true to send anyway."
        )

    subscribers = await newsletter_service.list_active_subscribers(db)
    return await sender.send_newsletter(db, content, subscribers=subscribers)


# --- Operations --------------------------------------------------------------


@router.get("/stats/weekly")
async def weekly_stats(db: DbSession, _admin: AdminUser) -> dict[str, object]:
    return await admin_service.weekly_stats(db)


@router.post("/embeddings/generate")
async def generate_embeddings(
    _admin: AdminUser,
    batch_size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, object]:
    """Queue an embedding backfill for jobs that have none."""
    from workers.embeddings import generate_job_embeddings

    task = generate_job_embeddings.delay(batch_size=batch_size)
    return {"queued": True, "task_id": task.id, "batch_size": batch_size}


@router.post("/sources/{source_id}/crawl-now")
async def crawl_now(
    source_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> dict[str, object]:
    """Crawl one source right now, inline, and return the outcome.

    Runs the crawl in-process rather than dispatching to Celery: this deployment
    has no worker, so a queued task would never run — the button used to look
    like it worked while doing nothing. A fresh session isolates the crawl's
    transaction from this request's, matching how the scheduler tick crawls each
    source. This bypasses the queue, so a source stuck behind a long backlog can
    be forced immediately.
    """
    source = await source_service.get_source(db, source_id)

    from app.database import AsyncSessionLocal
    from workers.crawl import _crawl

    async with AsyncSessionLocal() as crawl_db:
        result = await _crawl(crawl_db, str(source.id))
    return {"crawled": True, "url": source.url, "result": result}
