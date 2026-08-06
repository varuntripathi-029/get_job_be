"""Source routes — user submission and their own submission history.

Admin-facing source management lives in `app/admin/router.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.common.pagination import Page, PageParams, page_params
from app.database import get_db
from app.sources import service
from app.sources.schemas import SourceResponse, SourceSubmit

router = APIRouter(prefix="/sources", tags=["sources"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PageParams, Depends(page_params)]


@router.post(
    "/submit", response_model=SourceResponse, status_code=status.HTTP_201_CREATED
)
async def submit_source(
    data: SourceSubmit, db: DbSession, user: CurrentUser
) -> SourceResponse:
    """Submit a source for admin review. URL is SSRF-validated before saving."""
    source = await service.submit_source(db, user.id, data)
    return SourceResponse.model_validate(source)


@router.get("/my-submissions", response_model=Page[SourceResponse])
async def my_submissions(
    db: DbSession, params: Pagination, user: CurrentUser
) -> Page[SourceResponse]:
    sources, total = await service.list_sources(db, params, submitted_by=user.id)
    return Page.build(
        [SourceResponse.model_validate(s) for s in sources], total, params
    )
