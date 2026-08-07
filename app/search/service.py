"""Cross-entity search.

Two different strategies on purpose. Companies and events use ILIKE: the row
counts are small (hundreds), the fields are short, and users search them by
name, where substring matching beats stemming — "razor" should find "Razorpay",
which full-text search would not. Jobs use the existing `ix_jobs_fts` GIN index,
because descriptions are long prose where ranking and stemming genuinely help.
"""

from __future__ import annotations

import logging

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import PaginationParams, count_query
from app.companies.models import Company
from app.extraction.models import Event
from app.jobs.models import Job
from app.scoring.models import CompanyScore
from app.search.schemas import (
    PREVIEW_SIZE,
    CompanySearchResult,
    EventSearchResult,
    JobSearchResult,
    SearchResponse,
    SearchSection,
)

logger = logging.getLogger(__name__)


def _like_pattern(query: str) -> str:
    """Escape LIKE wildcards so a literal % or _ does not match everything."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def _latest_scores_subquery():
    """One row per company: its most recent score."""
    return (
        select(
            CompanyScore.company_id,
            CompanyScore.momentum_score,
            CompanyScore.momentum_label,
        )
        .distinct(CompanyScore.company_id)
        .order_by(CompanyScore.company_id, CompanyScore.scored_at.desc())
        .subquery("latest_scores")
    )


def _company_stmt(query: str) -> Select:
    pattern = _like_pattern(query)
    scores = _latest_scores_subquery()
    return (
        select(Company, scores.c.momentum_score, scores.c.momentum_label)
        .outerjoin(scores, scores.c.company_id == Company.id)
        .where(
            Company.is_active.is_(True),
            or_(
                func.lower(Company.name).like(pattern, escape="\\"),
                func.lower(Company.slug).like(pattern, escape="\\"),
                func.lower(Company.canonical_domain).like(pattern, escape="\\"),
                func.lower(Company.description).like(pattern, escape="\\"),
                # Exact alias match — aliases are short, canonical name variants.
                Company.aliases.any(query),
            ),
        )
        .order_by(Company.name)
    )


def _job_stmt(query: str) -> Select:
    """Full-text over title + description, ranked by relevance.

    The tsvector expression is written to match `ix_jobs_fts` exactly; any
    divergence silently drops the index and forces a sequential scan.
    """
    document = func.to_tsvector(
        "english",
        Job.title + " " + func.coalesce(Job.description_text, ""),
    )
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(document, tsquery).label("rank")

    return (
        select(Job, Company.name, Company.slug, rank)
        .join(Company, Company.id == Job.company_id)
        .where(Job.is_active.is_(True), document.op("@@")(tsquery))
        .order_by(rank.desc(), Job.first_seen_at.desc())
    )


def _event_stmt(query: str) -> Select:
    pattern = _like_pattern(query)
    return (
        select(Event, Company.name, Company.slug)
        .join(Company, Company.id == Event.company_id)
        .where(
            Event.is_canonical.is_(True),
            Event.status == "active",
            or_(
                func.lower(Event.title).like(pattern, escape="\\"),
                func.lower(Company.name).like(pattern, escape="\\"),
            ),
        )
        .order_by(Event.observed_at.desc())
    )


def _to_company(row) -> CompanySearchResult:
    company, score, label = row
    return CompanySearchResult(
        id=company.id,
        slug=company.slug,
        name=company.name,
        industry=company.industry,
        stage=company.stage,
        momentum_score=score,
        momentum_label=label,
    )


def _to_job(row) -> JobSearchResult:
    job, company_name, company_slug, _rank = row
    payload = JobSearchResult.model_validate(job)
    payload.company_name = company_name
    payload.company_slug = company_slug
    return payload


def _to_event(row) -> EventSearchResult:
    event, company_name, company_slug = row
    payload = EventSearchResult.model_validate(event)
    payload.company_name = company_name
    payload.company_slug = company_slug
    return payload


async def _run_section(
    db: AsyncSession,
    stmt: Select,
    mapper,
    *,
    limit: int,
    offset: int,
) -> SearchSection:
    total = await count_query(db, stmt)
    result = await db.execute(stmt.limit(limit).offset(offset))
    return SearchSection(items=[mapper(row) for row in result.all()], total=total)


async def search(
    db: AsyncSession,
    query: str,
    entity_type: str,
    params: PaginationParams,
) -> SearchResponse:
    """Search companies, jobs and events.

    With an explicit `entity_type` the matching section is fully paginated and
    the others come back empty. With `all`, each section returns a short preview
    plus its true total.
    """
    query = query.strip()

    empty: SearchSection = SearchSection(items=[], total=0)
    companies = jobs = events = empty

    wants = {
        "company": entity_type in ("all", "company"),
        "job": entity_type in ("all", "job"),
        "event": entity_type in ("all", "event"),
    }
    preview = entity_type == "all"
    limit = PREVIEW_SIZE if preview else params.limit
    offset = 0 if preview else params.offset

    if wants["company"]:
        companies = await _run_section(
            db, _company_stmt(query), _to_company, limit=limit, offset=offset
        )
    if wants["job"]:
        jobs = await _run_section(
            db, _job_stmt(query), _to_job, limit=limit, offset=offset
        )
    if wants["event"]:
        events = await _run_section(
            db, _event_stmt(query), _to_event, limit=limit, offset=offset
        )

    total = companies.total + jobs.total + events.total
    logger.info(
        "search %r type=%s -> %d companies, %d jobs, %d events",
        query,
        entity_type,
        companies.total,
        jobs.total,
        events.total,
    )

    return SearchResponse(
        query=query,
        type=entity_type,  # type: ignore[arg-type]
        companies=companies,
        jobs=jobs,
        events=events,
        total=total,
        page=params.page,
        per_page=params.per_page,
    )
