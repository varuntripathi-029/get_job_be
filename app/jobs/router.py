"""Job routes — public reads."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import PaginatedResponse, PaginationParams, get_pagination
from app.database import get_db
from app.jobs import service
from app.jobs.models import ROLE_FAMILIES, SENIORITIES, WORK_MODES
from app.jobs.schemas import JobDetailResponse, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


@router.get("", response_model=PaginatedResponse[JobResponse], summary="List jobs")
async def list_jobs(
    db: DbSession,
    params: Pagination,
    company_slug: Annotated[str | None, Query()] = None,
    role_family: Annotated[str | None, Query(pattern="|".join(ROLE_FAMILIES))] = None,
    seniority: Annotated[str | None, Query(pattern="|".join(SENIORITIES))] = None,
    work_mode: Annotated[str | None, Query(pattern="|".join(WORK_MODES))] = None,
    is_active: Annotated[bool | None, Query()] = True,
    search: Annotated[str | None, Query(max_length=100)] = None,
    sort_by: Annotated[
        str, Query(pattern="^(first_seen_at|last_seen_at|title)$")
    ] = "first_seen_at",
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> PaginatedResponse[JobResponse]:
    """Open roles synced from company ATS boards."""
    return await service.list_jobs(
        db,
        params,
        company_slug=company_slug,
        role_family=role_family,
        seniority=seniority,
        work_mode=work_mode,
        is_active=is_active,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/{job_id}", response_model=JobDetailResponse, summary="Job detail")
async def get_job(job_id: uuid.UUID, db: DbSession) -> JobDetailResponse:
    return await service.get_job(db, job_id)
