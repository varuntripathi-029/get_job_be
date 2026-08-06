"""Company CRUD and entity resolution."""

import re
import unicodedata
import uuid
from urllib.parse import urlparse

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError
from app.common.pagination import PageParams
from app.companies.models import Company
from app.companies.schemas import CompanyCreate, CompanyUpdate
from app.scoring.models import CompanyScore

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


def _apply_filters(
    stmt: Select,
    *,
    industry: str | None,
    stage: str | None,
    is_active: bool | None,
    search: str | None,
) -> Select:
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
    return stmt


async def list_companies(
    db: AsyncSession,
    params: PageParams,
    *,
    industry: str | None = None,
    stage: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
) -> tuple[list[Company], int]:
    filters = {
        "industry": industry,
        "stage": stage,
        "is_active": is_active,
        "search": search,
    }

    total = await db.scalar(
        _apply_filters(select(func.count()).select_from(Company), **filters)
    )
    result = await db.execute(
        _apply_filters(select(Company), **filters)
        .order_by(Company.name)
        .limit(params.limit)
        .offset(params.offset)
    )
    return list(result.scalars().all()), int(total or 0)


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
