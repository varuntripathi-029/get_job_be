"""Event routes — public reads of canonical, active signals."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import PaginatedResponse, PaginationParams, get_pagination
from app.database import get_db
from app.extraction import service
from app.extraction.models import EVENT_TYPES
from app.extraction.schemas import EventResponse

router = APIRouter(prefix="/events", tags=["events"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get(
    "", response_model=PaginatedResponse[EventResponse], summary="List signals"
)
async def list_events(
    db: DbSession,
    params: Pagination,
    company_slug: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query(pattern="|".join(EVENT_TYPES))] = None,
    days: Annotated[
        int,
        Query(ge=1, le=3650, description="Only events observed in the last N days."),
    ] = 30,
    sort_by: Annotated[
        str, Query(pattern="^(observed_at|event_occurred_at)$")
    ] = "observed_at",
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> PaginatedResponse[EventResponse]:
    """Hiring signals extracted from public sources. Each carries its evidence."""
    return await service.list_events(
        db,
        params,
        company_slug=company_slug,
        event_type=event_type,
        days=days,
        sort_by=sort_by,
        sort_order=sort_order,
    )
