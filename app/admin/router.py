"""Admin routes — source moderation, crawler health, metrics."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service as admin_service
from app.auth.dependencies import AdminUser
from app.common.pagination import Page, PageParams, page_params
from app.database import get_db
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
Pagination = Annotated[PageParams, Depends(page_params)]


@router.get("/sources/pending", response_model=Page[SourceResponse])
async def pending_sources(
    db: DbSession, params: Pagination, _admin: AdminUser
) -> Page[SourceResponse]:
    sources, total = await source_service.list_sources(db, params, status="pending")
    return Page.build(
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
) -> list[CrawlerHealthRow]:
    return await admin_service.crawler_health(
        db, limit=limit, only_failing=only_failing
    )


@router.get("/metrics")
async def instance_metrics(db: DbSession, _admin: AdminUser) -> dict[str, object]:
    return await admin_service.instance_metrics(db)
