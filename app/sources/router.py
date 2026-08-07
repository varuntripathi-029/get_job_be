"""Source routes — public coverage views, user submission, own history.

Admin-facing source management lives in `app/admin/router.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.common.pagination import PaginatedResponse, PaginationParams, get_pagination
from app.database import get_db
from app.sources import service
from app.sources.schemas import (
    SourceBrowseResponse,
    SourceResponse,
    SourceStats,
    SourceSubmit,
)

router = APIRouter(prefix="/sources", tags=["sources"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get(
    "/browse", response_model=SourceBrowseResponse, summary="What is being tracked"
)
async def browse(db: DbSession) -> SourceBrowseResponse:
    """Approved sources grouped by company, so gaps are visible and submittable."""
    return await service.browse_sources(db)


@router.get("/stats", response_model=SourceStats, summary="Coverage summary")
async def stats(db: DbSession) -> SourceStats:
    return await service.source_stats(db)


@router.post(
    "/submit", response_model=SourceResponse, status_code=status.HTTP_201_CREATED
)
async def submit_source(
    data: SourceSubmit, db: DbSession, user: CurrentUser
) -> SourceResponse:
    """Submit a source for admin review. URL is SSRF-validated before saving."""
    source = await service.submit_source(db, user.id, data)
    return SourceResponse.model_validate(source)


@router.get("/my-submissions", response_model=PaginatedResponse[SourceResponse])
async def my_submissions(
    db: DbSession, params: Pagination, user: CurrentUser
) -> PaginatedResponse[SourceResponse]:
    sources, total = await service.list_sources(db, params, submitted_by=user.id)
    return PaginatedResponse.build(
        [SourceResponse.model_validate(s) for s in sources], total, params
    )
