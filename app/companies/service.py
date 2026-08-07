"""Company CRUD and entity resolution."""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.pagination import PaginatedResponse, PaginationParams, paginate
from app.companies.models import Company
from app.companies.schemas import (
    CompanyComparison,
    CompanyCreate,
    CompanyListItem,
    CompanyUpdate,
    ScorePoint,
)
from app.extraction.models import Event
from app.jobs.models import Job
from app.scoring.models import CompanyScore
from app.sources.models import Source

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """ASCII, lowercase, hyphen-separated."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-") or "company"


def normalize_domain(value: str) -> str:
    """Reduce a URL or bare host to a comparable registrable-ish domain."""
    candidate = value.strip().lower()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or candidate
    candidate = candidate.split("/")[0].rstrip(".")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate


async def _unique_slug(db: AsyncSession, base: str) -> str:
    """Append -2, -3 … until the slug is free."""
    slug = base
    suffix = 1
    while True:
        exists = await db.scalar(select(Company.id).where(Company.slug == slug))
        if exists is None:
            return slug
        suffix += 1
        slug = f"{base}-{suffix}"


async def create_company(db: AsyncSession, data: CompanyCreate) -> Company:
    domain = normalize_domain(data.canonical_domain)

    clash = await db.scalar(
        select(Company.id).where(Company.canonical_domain == domain)
    )
    if clash is not None:
        raise ConflictError(f"A company already exists for domain {domain!r}.")

    slug = await _unique_slug(db, data.slug or slugify(data.name))

    company = Company(
        **data.model_dump(exclude={"slug", "canonical_domain"}),
        slug=slug,
        canonical_domain=domain,
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company


async def get_company_by_slug(db: AsyncSession, slug: str) -> Company:
    company = await db.scalar(select(Company).where(Company.slug == slug))
    if company is None:
        raise NotFoundError(f"No company with slug {slug!r}.")
    return company


async def get_company_by_id(db: AsyncSession, company_id: uuid.UUID) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise NotFoundError(f"No company with id {company_id}.")
    return company


def _latest_scores():
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


def _active_job_count():
    """Correlated count, so companies with no jobs are still returned."""
    return (
        select(func.count())
        .select_from(Job)
        .where(Job.company_id == Company.id, Job.is_active.is_(True))
        .scalar_subquery()
    )


LIST_SORTS = ("name", "momentum_score", "active_jobs", "created_at")


def _company_list_stmt(
    *,
    industry: str | None = None,
    stage: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    min_score: float | None = None,
    has_active_jobs: bool | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> Select:
    scores = _latest_scores()
    job_count = _active_job_count().label("active_job_count")

    stmt = select(
        Company, scores.c.momentum_score, scores.c.momentum_label, job_count
    ).outerjoin(scores, scores.c.company_id == Company.id)

    if industry:
        stmt = stmt.where(Company.industry == industry)
    if stage:
        stmt = stmt.where(Company.stage == stage)
    if is_active is not None:
        stmt = stmt.where(Company.is_active.is_(is_active))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Company.name).like(pattern),
                func.lower(Company.canonical_domain).like(pattern),
            )
        )
    if min_score is not None:
        stmt = stmt.where(scores.c.momentum_score >= min_score)
    if has_active_jobs is not None:
        # Compared against the subquery expression, not the label: a label is
        # not addressable in WHERE.
        condition = _active_job_count() > 0
        stmt = stmt.where(condition if has_active_jobs else ~condition)

    column = {
        "name": Company.name,
        "momentum_score": scores.c.momentum_score,
        "active_jobs": _active_job_count(),
        "created_at": Company.created_at,
    }[sort_by]

    if sort_order == "desc":
        # Unscored companies sort last in both directions rather than bubbling
        # to the top as NULLs.
        order = column.desc().nullslast()
    else:
        order = column.asc().nullslast()

    return stmt.order_by(order, Company.id)


async def list_companies(
    db: AsyncSession,
    params: PaginationParams,
    *,
    industry: str | None = None,
    stage: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    min_score: float | None = None,
    has_active_jobs: bool | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> PaginatedResponse[CompanyListItem]:
    if sort_by not in LIST_SORTS:
        raise ValidationError(
            f"sort_by must be one of {', '.join(LIST_SORTS)}, got {sort_by!r}."
        )

    stmt = _company_list_stmt(
        industry=industry,
        stage=stage,
        is_active=is_active,
        search=search,
        min_score=min_score,
        has_active_jobs=has_active_jobs,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return await paginate(db, stmt, params, _to_list_item)


def _to_list_item(row) -> CompanyListItem:
    company, score, label, job_count = row
    payload = CompanyListItem.model_validate(company)
    payload.momentum_score = score
    payload.momentum_label = label
    payload.active_job_count = int(job_count or 0)
    return payload


async def update_company(
    db: AsyncSession, slug: str, data: CompanyUpdate
) -> Company:
    company = await get_company_by_slug(db, slug)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    await db.commit()
    await db.refresh(company)
    return company


async def resolve_company(db: AsyncSession, name_or_domain: str) -> Company | None:
    """Entity resolution for news articles and other unstructured mentions.

    Tries the cheapest, most reliable signal first: exact domain, then exact
    name, then the aliases array. Returns None rather than guessing — a wrong
    match attaches someone else's funding round to a company.
    """
    if not name_or_domain or not name_or_domain.strip():
        return None

    candidate = name_or_domain.strip()

    domain = normalize_domain(candidate)
    if domain:
        company = await db.scalar(
            select(Company).where(Company.canonical_domain == domain)
        )
        if company is not None:
            return company

    company = await db.scalar(
        select(Company).where(func.lower(Company.name) == candidate.lower())
    )
    if company is not None:
        return company

    company = await db.scalar(
        select(Company).where(Company.aliases.any(candidate))
    )
    if company is not None:
        return company

    return await db.scalar(select(Company).where(Company.slug == slugify(candidate)))


async def get_latest_score(
    db: AsyncSession, company_id: uuid.UUID
) -> CompanyScore | None:
    return await db.scalar(
        select(CompanyScore)
        .where(CompanyScore.company_id == company_id)
        .order_by(CompanyScore.scored_at.desc())
        .limit(1)
    )


async def get_score_history(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    limit: int = 10,
    days: int | None = None,
    newest_first: bool = False,
) -> list[CompanyScore]:
    """The last N scores for a sparkline.

    Always selected newest-first so the limit keeps the most recent points, then
    reversed unless the caller wants them newest-first. Charts want oldest-first
    so they can plot without reversing; the history endpoint wants newest-first.
    """
    stmt = select(CompanyScore).where(CompanyScore.company_id == company_id)
    if days is not None:
        stmt = stmt.where(
            CompanyScore.scored_at >= datetime.now(UTC) - timedelta(days=days)
        )
    result = await db.execute(
        stmt.order_by(CompanyScore.scored_at.desc()).limit(limit)
    )
    rows = list(result.scalars().all())
    return rows if newest_first else list(reversed(rows))


MIN_COMPARE = 2
MAX_COMPARE = 5


async def compare_companies(
    db: AsyncSession, slugs: list[str]
) -> list[CompanyComparison]:
    """Side-by-side data for 2-5 companies.

    Every slug must resolve; a silently dropped company would leave the frontend
    rendering fewer columns than the user asked for with no explanation.
    """
    cleaned = [s.strip() for s in slugs if s.strip()]
    # Preserve request order but drop repeats, so ?slugs=a,a,b is two columns.
    unique = list(dict.fromkeys(cleaned))

    if len(unique) < MIN_COMPARE:
        raise ValidationError(
            f"Comparison needs at least {MIN_COMPARE} distinct companies, "
            f"got {len(unique)}."
        )
    if len(unique) > MAX_COMPARE:
        raise ValidationError(
            f"Comparison supports at most {MAX_COMPARE} companies, got {len(unique)}."
        )

    result = await db.execute(select(Company).where(Company.slug.in_(unique)))
    by_slug = {c.slug: c for c in result.scalars().all()}

    missing = [slug for slug in unique if slug not in by_slug]
    if missing:
        names = ", ".join(repr(m) for m in missing)
        raise NotFoundError(f"No company with slug {names}.")

    return [await _comparison_for(db, by_slug[slug]) for slug in unique]


async def _comparison_for(db: AsyncSession, company: Company) -> CompanyComparison:
    from app.extraction.service import recent_events_for_company

    score = await get_latest_score(db, company.id)

    # Grouped on the raw column, with NULL folded into "other" afterwards.
    # coalesce(role_family, 'other') in both SELECT and GROUP BY looks
    # equivalent but is not: SQLAlchemy renders the literal as a bind parameter,
    # and Postgres will not match $1 against $3, so it rejects the query with
    # "column jobs.role_family must appear in the GROUP BY clause".
    family_rows = (
        await db.execute(
            select(Job.role_family, func.count(Job.id))
            .where(Job.company_id == company.id, Job.is_active.is_(True))
            .group_by(Job.role_family)
        )
    ).all()
    by_family: dict[str, int] = {}
    for family, count in family_rows:
        key = family or "other"
        by_family[key] = by_family.get(key, 0) + int(count)

    since = datetime.now(UTC) - timedelta(days=30)
    recent_count = int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.company_id == company.id,
                Event.observed_at >= since,
                Event.is_canonical.is_(True),
                Event.status == "active",
            )
        )
        or 0
    )

    history = await get_score_history(db, company.id, limit=10)

    return CompanyComparison(
        slug=company.slug,
        name=company.name,
        industry=company.industry,
        stage=company.stage,
        momentum_score=score.momentum_score if score else None,
        momentum_label=score.momentum_label if score else None,
        active_jobs=sum(by_family.values()),
        active_jobs_by_family=by_family,
        recent_events=recent_count,
        score_history=[ScorePoint.model_validate(s) for s in history],
        top_events=await recent_events_for_company(db, company.id, limit=3),
    )


async def get_company_sources(db: AsyncSession, company_id: uuid.UUID) -> list[Source]:
    """Approved sources feeding a company, for the detail page."""
    result = await db.execute(
        select(Source)
        .where(Source.company_id == company_id, Source.status == "approved")
        .order_by(Source.source_type, Source.url)
    )
    return list(result.scalars().all())


async def count_events(db: AsyncSession, company_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.company_id == company_id,
                Event.is_canonical.is_(True),
                Event.status == "active",
            )
        )
        or 0
    )
