"""Search routes — public."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import PaginationParams, get_pagination
from app.common.rate_limit import rate_limit_search
from app.database import get_db
from app.search import service
from app.search.schemas import MIN_QUERY_LENGTH, SearchResponse

router = APIRouter(prefix="/search", tags=["search"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get(
    "",
    response_model=SearchResponse,
    summary="Search companies, jobs and events",
    dependencies=[Depends(rate_limit_search)],
)
async def search(
    db: DbSession,
    params: Pagination,
    q: Annotated[
        str,
        Query(
            min_length=MIN_QUERY_LENGTH,
            max_length=200,
            description="Search text. Companies and events match on substring; "
            "jobs use full-text ranking over title and description.",
        ),
    ],
    type: Annotated[
        str,
        Query(
            pattern="^(all|company|job|event)$",
            description="Restrict to one entity type. With 'all', each section "
            "returns a 5-item preview plus its full total.",
        ),
    ] = "all",
) -> SearchResponse:
    return await service.search(db, q, type, params)
