"""Company routes — public reads, admin writes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser
from app.common.pagination import (
    PaginatedResponse,
    PaginationParams,
    get_pagination,
)
from app.companies import service
from app.companies.models import STAGES
from app.companies.schemas import (
    CompanyCreate,
    CompanyDetailResponse,
    CompanyListItem,
    CompanyResponse,
    CompanySourceSummary,
    CompanyUpdate,
    CompareResponse,
    ScorePoint,
)
from app.database import get_db
from app.extraction import service as extraction_service
from app.extraction.schemas import EventResponse
from app.jobs import service as job_service
from app.jobs.schemas import JobResponse

router = APIRouter(prefix="/companies", tags=["companies"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get(
    "",
    response_model=PaginatedResponse[CompanyListItem],
    summary="List companies",
)
async def list_companies(
    db: DbSession,
    params: Pagination,
    industry: Annotated[str | None, Query()] = None,
    stage: Annotated[str | None, Query(pattern="|".join(STAGES))] = None,
    is_active: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    has_active_jobs: Annotated[bool | None, Query()] = None,
    sort_by: Annotated[
        str, Query(pattern="^(name|momentum_score|active_jobs|created_at)$")
    ] = "name",
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
) -> PaginatedResponse[CompanyListItem]:
    """Each row carries its latest momentum score and active job count."""
    return await service.list_companies(
        db,
        params,
        industry=industry,
        stage=stage,
        is_active=is_active,
        search=search,
        min_score=min_score,
        has_active_jobs=has_active_jobs,
        sort_by=sort_by,
        sort_order=sort_order,
    )


# Declared before /{slug}: FastAPI matches routes in definition order, so a
# /{slug} route defined first would swallow "compare" as a company slug.
@router.get(
    "/compare", response_model=CompareResponse, summary="Compare 2-5 companies"
)
async def compare(
    db: DbSession,
    slugs: Annotated[
        str, Query(description="Comma-separated slugs, e.g. razorpay,cred,zerodha")
    ],
) -> CompareResponse:
    return CompareResponse(
        companies=await service.compare_companies(db, slugs.split(","))
    )


@router.get(
    "/{slug}", response_model=CompanyDetailResponse, summary="Company detail"
)
async def get_company(slug: str, db: DbSession) -> CompanyDetailResponse:
    """Everything the company page needs in one round trip."""
    company = await service.get_company_by_slug(db, slug)
    payload = CompanyDetailResponse.model_validate(company)

    score = await service.get_latest_score(db, company.id)
    if score is not None:
        payload.momentum_score = score.momentum_score
        payload.momentum_label = score.momentum_label
        payload.score_delta = score.score_delta
        payload.scored_at = score.scored_at

    payload.score_history = [
        ScorePoint.model_validate(s)
        for s in await service.get_score_history(db, company.id, limit=10)
    ]
    payload.active_job_count = await job_service.count_active_jobs(db, company.id)
    payload.total_event_count = await service.count_events(db, company.id)
    payload.recent_events = await extraction_service.recent_events_for_company(
        db, company.id, limit=5
    )
    payload.sources = [
        CompanySourceSummary.model_validate(s)
        for s in await service.get_company_sources(db, company.id)
    ]
    return payload


@router.get(
    "/{slug}/jobs",
    response_model=PaginatedResponse[JobResponse],
    summary="Jobs at one company",
)
async def company_jobs(
    slug: str,
    db: DbSession,
    params: Pagination,
    is_active: Annotated[bool | None, Query()] = True,
) -> PaginatedResponse[JobResponse]:
    # Resolve first so an unknown slug is a 404 rather than an empty page.
    await service.get_company_by_slug(db, slug)
    return await job_service.list_jobs(
        db, params, company_slug=slug, is_active=is_active
    )


@router.get(
    "/{slug}/events",
    response_model=PaginatedResponse[EventResponse],
    summary="Signals for one company",
)
async def company_events(
    slug: str,
    db: DbSession,
    params: Pagination,
    event_type: Annotated[str | None, Query()] = None,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
) -> PaginatedResponse[EventResponse]:
    await service.get_company_by_slug(db, slug)
    return await extraction_service.list_events(
        db, params, company_slug=slug, event_type=event_type, days=days
    )


@router.get(
    "/{slug}/score-history",
    response_model=list[ScorePoint],
    summary="Momentum history",
    tags=["scores"],
)
async def score_history(
    slug: str,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[ScorePoint]:
    """Score points for a sparkline, newest first."""
    company = await service.get_company_by_slug(db, slug)
    history = await service.get_score_history(
        db, company.id, limit=limit, days=days, newest_first=True
    )
    return [ScorePoint.model_validate(s) for s in history]


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    data: CompanyCreate, db: DbSession, _admin: AdminUser
) -> CompanyResponse:
    company = await service.create_company(db, data)
    return CompanyResponse.model_validate(company)


@router.patch("/{slug}", response_model=CompanyResponse)
async def update_company(
    slug: str, data: CompanyUpdate, db: DbSession, _admin: AdminUser
) -> CompanyResponse:
    company = await service.update_company(db, slug, data)
    return CompanyResponse.model_validate(company)
