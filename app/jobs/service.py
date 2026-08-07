"""Job reads. Writes belong to the ATS sync worker, not to the API."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError, ValidationError
from app.common.pagination import PaginatedResponse, PaginationParams, paginate
from app.companies.models import Company
from app.jobs.models import Job
from app.jobs.schemas import JobDetailResponse, JobResponse

SORTABLE = ("first_seen_at", "last_seen_at", "title")


def _base_stmt() -> Select:
    return select(Job, Company.name, Company.slug).join(
        Company, Company.id == Job.company_id
    )


async def list_jobs(
    db: AsyncSession,
    params: PaginationParams,
    *,
    company_slug: str | None = None,
    role_family: str | None = None,
    seniority: str | None = None,
    work_mode: str | None = None,
    is_active: bool | None = True,
    search: str | None = None,
    sort_by: str = "first_seen_at",
    sort_order: str = "desc",
) -> PaginatedResponse[JobResponse]:
    if sort_by not in SORTABLE:
        raise ValidationError(
            f"sort_by must be one of {', '.join(SORTABLE)}, got {sort_by!r}."
        )

    stmt = _base_stmt()
    if company_slug:
        stmt = stmt.where(Company.slug == company_slug)
    if role_family:
        stmt = stmt.where(Job.role_family == role_family)
    if seniority:
        stmt = stmt.where(Job.seniority == seniority)
    if work_mode:
        stmt = stmt.where(Job.work_mode == work_mode)
    if is_active is not None:
        stmt = stmt.where(Job.is_active.is_(is_active))
    if search:
        # Title-only substring match. Full-text ranking across descriptions is
        # what /search is for; this is a cheap filter on an already-narrow list.
        stmt = stmt.where(Job.title.ilike(f"%{search}%"))

    column = getattr(Job, sort_by)
    order = column.desc() if sort_order == "desc" else column.asc()
    # Tiebreak on id so pagination stays stable across equal sort values.
    stmt = stmt.order_by(order, Job.id.desc())

    return await paginate(db, stmt, params, _to_response)


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> JobDetailResponse:
    result = await db.execute(_base_stmt().where(Job.id == job_id))
    row = result.first()
    if row is None:
        raise NotFoundError(f"No job with id {job_id}.")

    job, company_name, company_slug = row
    payload = JobDetailResponse.model_validate(job)
    payload.company_name = company_name
    payload.company_slug = company_slug
    return payload


async def count_active_jobs(db: AsyncSession, company_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.company_id == company_id, Job.is_active.is_(True))
        )
        or 0
    )


def _to_response(row) -> JobResponse:
    job, company_name, company_slug = row
    payload = JobResponse.model_validate(job)
    payload.company_name = company_name
    payload.company_slug = company_slug
    return payload
