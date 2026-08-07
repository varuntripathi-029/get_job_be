"""Dashboard routes — public, cached aggregates for the landing page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard import service
from app.dashboard.schemas import (
    ActivityResponse,
    DashboardStats,
    IndustriesResponse,
    TrendingResponse,
)
from app.database import get_db
from app.extraction.models import EVENT_TYPES

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/stats", response_model=DashboardStats, summary="Platform totals")
async def stats(db: DbSession) -> DashboardStats:
    """Headline counts for the hero section. Cached for 5 minutes."""
    return await service.get_stats(db)


@router.get("/trending", response_model=TrendingResponse, summary="Trending companies")
async def trending(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
    industry: Annotated[str | None, Query()] = None,
) -> TrendingResponse:
    """Companies whose momentum rose most in the last 7 days. Cached 10 minutes."""
    return TrendingResponse(
        trending=await service.trending_companies(db, limit=limit, industry=industry)
    )


@router.get("/activity", response_model=ActivityResponse, summary="Recent signals")
async def activity(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    event_type: Annotated[str | None, Query(pattern="|".join(EVENT_TYPES))] = None,
) -> ActivityResponse:
    """Live feed of the newest hiring signals. Not cached."""
    return ActivityResponse(
        events=await service.recent_activity(db, limit=limit, event_type=event_type)
    )


@router.get(
    "/industries", response_model=IndustriesResponse, summary="Industry breakdown"
)
async def industries(db: DbSession) -> IndustriesResponse:
    """Company count and average momentum per industry. Cached 30 minutes."""
    return await service.industry_breakdown(db)
