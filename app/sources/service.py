"""Source registry: submission, approval, and fetch-tier detection."""

import logging
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.pagination import PaginationParams
from app.companies.models import Company
from app.crawler.ssrf import validate_url
from app.sources.models import Source
from app.sources.schemas import (
    CompanySourceGroup,
    SourceAdminCreate,
    SourceBrowseItem,
    SourceBrowseResponse,
    SourceStats,
    SourceSubmit,
    SourceUpdate,
)

logger = logging.getLogger(__name__)

# Default cadence per source type, in minutes.
DEFAULT_FREQUENCIES = {
    "ats_api": 720,
    "career_page": 1440,
    "rss_feed": 360,
    "company_blog": 1440,
    "engineering_blog": 2880,
    "news_site": 360,
    "news_api": 360,
    "search_api": 1440,
    "github_org": 4320,
}

# Prior belief about how trustworthy each source type is. Feeds the scoring
# engine's tier factor.
DEFAULT_RELIABILITY = {
    "ats_api": 1.0,
    "career_page": 0.9,
    "company_blog": 0.9,
    "engineering_blog": 0.8,
    "news_site": 0.8,
    "rss_feed": 0.7,
    "news_api": 0.7,
    "search_api": 0.7,
    "github_org": 0.5,
}

# host pattern -> (provider, board-token extractor, API URL template)
_ATS_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"^(?:boards|job-boards)\.greenhouse\.io/(?P<token>[^/?#]+)"),
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    ),
    (
        re.compile(r"^boards-api\.greenhouse\.io/v1/boards/(?P<token>[^/?#]+)"),
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    ),
    (
        re.compile(r"^jobs\.lever\.co/(?P<token>[^/?#]+)"),
        "lever",
        "https://api.lever.co/v0/postings/{token}?mode=json",
    ),
    (
        re.compile(r"^api\.lever\.co/v0/postings/(?P<token>[^/?#]+)"),
        "lever",
        "https://api.lever.co/v0/postings/{token}?mode=json",
    ),
    (
        re.compile(r"^jobs\.ashbyhq\.com/(?P<token>[^/?#]+)"),
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
    ),
    (
        re.compile(r"^api\.ashbyhq\.com/posting-api/job-board/(?P<token>[^/?#]+)"),
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
    ),
    (
        re.compile(r"^apply\.workable\.com/(?P<token>[^/?#]+)"),
        "workable",
        "https://apply.workable.com/api/v1/companies/{token}/jobs",
    ),
    (
        re.compile(r"^(?P<token>[^.]+)\.recruitee\.com"),
        "recruitee",
        "https://{token}.recruitee.com/api/offers/",
    ),
    (
        re.compile(r"^(?P<token>[^.]+)\.breezy\.hr"),
        "breezy",
        "https://{token}.breezy.hr/positions?format=json",
    ),
    (
        re.compile(r"^(?P<token>[^.]+)\.freshteam\.com"),
        "freshteam",
        "https://{token}.freshteam.com/jobs.json",
    ),
    (
        re.compile(r"^(?P<token>[^.]+)\.pinpointhq\.com"),
        "pinpoint",
        "https://{token}.pinpointhq.com/jobs.json",
    ),
]

# Internal identifiers, not real URLs — these never hit SSRF validation.
_PSEUDO_SCHEMES = ("newsapi://", "gnews://", "serpapi://")

_RSS_HINTS = ("/feed", "/rss", "feed.xml", "rss.xml", "atom.xml", "/index.xml", ".cms")


def is_pseudo_url(url: str) -> bool:
    """True for news/search API identifiers like `newsapi://search`."""
    return url.lower().startswith(_PSEUDO_SCHEMES)


def detect_ats_provider(url: str) -> tuple[str, str] | None:
    """Return (provider, json_api_url) when the URL is a known ATS board."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    target = f"{host}{parsed.path}"

    for pattern, provider, template in _ATS_PATTERNS:
        match = pattern.match(target)
        if match:
            return provider, template.format(token=match.group("token"))
    return None


def detect_fetch_tier(url: str, source_type: str | None = None) -> str:
    """Best-guess fetch tier. Always overridable by an admin."""
    lowered = url.lower()

    if lowered.startswith(("newsapi://", "gnews://")):
        return "news_api"
    if lowered.startswith("serpapi://"):
        return "search_api"
    if detect_ats_provider(url) is not None:
        return "ats_api"
    if any(hint in lowered for hint in _RSS_HINTS):
        return "rss"
    if source_type in ("news_api", "search_api", "ats_api", "rss_feed"):
        return {"rss_feed": "rss"}.get(source_type, source_type)
    return "static_http"


async def _reject_duplicate(db: AsyncSession, url: str) -> None:
    existing = await db.scalar(select(Source.id).where(Source.url == url))
    if existing is not None:
        raise ConflictError(f"Source {url!r} is already registered.")


async def submit_source(
    db: AsyncSession, user_id: uuid.UUID, data: SourceSubmit
) -> Source:
    """User submission — lands in the pending queue for admin review."""
    if is_pseudo_url(data.url):
        raise ValidationError("Pseudo-URL sources can only be created by an admin.")

    # Raises SSRFError for private/internal targets before anything is persisted.
    url = validate_url(data.url)
    await _reject_duplicate(db, url)

    source = Source(
        company_id=data.company_id,
        url=url,
        source_type=data.source_type,
        fetch_tier=data.fetch_tier or detect_fetch_tier(url, data.source_type),
        status="pending",
        submitted_by=user_id,
        crawl_frequency_minutes=DEFAULT_FREQUENCIES.get(data.source_type, 1440),
        reliability_score=DEFAULT_RELIABILITY.get(data.source_type, 0.7),
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def create_source_as_admin(
    db: AsyncSession, data: SourceAdminCreate
) -> Source:
    """Admin-created source — approved immediately and due for a first crawl."""
    url = data.url if is_pseudo_url(data.url) else validate_url(data.url)
    await _reject_duplicate(db, url)

    source = Source(
        company_id=data.company_id,
        url=url,
        source_type=data.source_type,
        fetch_tier=data.fetch_tier or detect_fetch_tier(url, data.source_type),
        status="approved",
        next_crawl_at=datetime.now(UTC),
        crawl_frequency_minutes=(
            data.crawl_frequency_minutes
            or DEFAULT_FREQUENCIES.get(data.source_type, 1440)
        ),
        reliability_score=(
            data.reliability_score
            if data.reliability_score is not None
            else DEFAULT_RELIABILITY.get(data.source_type, 0.7)
        ),
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> Source:
    source = await db.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"No source with id {source_id}.")
    return source


async def approve_source(
    db: AsyncSession, admin_id: uuid.UUID, source_id: uuid.UUID
) -> Source:
    source = await get_source(db, source_id)

    # DNS may have changed since submission, so re-validate before scheduling.
    if not is_pseudo_url(source.url):
        validate_url(source.url)

    source.status = "approved"
    source.next_crawl_at = datetime.now(UTC)
    source.rejection_reason = None
    await db.commit()
    await db.refresh(source)

    logger.info("source_approved id=%s by=%s url=%s", source.id, admin_id, source.url)
    return source


async def reject_source(
    db: AsyncSession, admin_id: uuid.UUID, source_id: uuid.UUID, reason: str
) -> Source:
    source = await get_source(db, source_id)
    source.status = "rejected"
    source.rejection_reason = reason
    source.next_crawl_at = None
    await db.commit()
    await db.refresh(source)

    logger.info("source_rejected id=%s by=%s reason=%s", source.id, admin_id, reason)
    return source


async def disable_source(db: AsyncSession, source_id: uuid.UUID) -> Source:
    source = await get_source(db, source_id)
    source.status = "disabled"
    source.next_crawl_at = None
    await db.commit()
    await db.refresh(source)
    return source


async def update_source(
    db: AsyncSession, source_id: uuid.UUID, data: SourceUpdate
) -> Source:
    source = await get_source(db, source_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)
    return source


def _apply_filters(
    stmt: Select,
    *,
    status: str | None,
    company_id: uuid.UUID | None,
    source_type: str | None,
    submitted_by: uuid.UUID | None,
) -> Select:
    if status:
        stmt = stmt.where(Source.status == status)
    if company_id:
        stmt = stmt.where(Source.company_id == company_id)
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    if submitted_by:
        stmt = stmt.where(Source.submitted_by == submitted_by)
    return stmt


async def list_sources(
    db: AsyncSession,
    params: PaginationParams,
    *,
    status: str | None = None,
    company_id: uuid.UUID | None = None,
    source_type: str | None = None,
    submitted_by: uuid.UUID | None = None,
) -> tuple[list[Source], int]:
    filters = {
        "status": status,
        "company_id": company_id,
        "source_type": source_type,
        "submitted_by": submitted_by,
    }

    total = await db.scalar(
        _apply_filters(select(func.count()).select_from(Source), **filters)
    )
    result = await db.execute(
        _apply_filters(select(Source), **filters)
        .order_by(Source.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    return list(result.scalars().all()), int(total or 0)


async def browse_sources(db: AsyncSession) -> SourceBrowseResponse:
    """Approved sources grouped by company, for the public coverage page.

    Lets a visitor see what is tracked and spot gaps worth submitting.
    """
    result = await db.execute(
        select(Source, Company.slug, Company.name)
        .outerjoin(Company, Company.id == Source.company_id)
        .where(Source.status == "approved")
        .order_by(Company.name.nullslast(), Source.source_type, Source.url)
    )

    groups: dict[str, CompanySourceGroup] = {}
    global_sources: list[SourceBrowseItem] = []

    for source, slug, name in result.all():
        item = SourceBrowseItem.model_validate(source)
        if slug is None:
            # company_id is NULL for news sites and news APIs, which cover many
            # companies at once.
            global_sources.append(item)
            continue
        if slug not in groups:
            groups[slug] = CompanySourceGroup(slug=slug, name=name, sources=[])
        groups[slug].sources.append(item)

    return SourceBrowseResponse(
        companies=list(groups.values()), global_sources=global_sources
    )


async def source_stats(db: AsyncSession) -> SourceStats:
    """Coverage summary: how many sources, of what kind, and which companies
    have none."""
    by_type = {
        row[0]: int(row[1])
        for row in (
            await db.execute(
                select(Source.source_type, func.count()).group_by(Source.source_type)
            )
        ).all()
    }
    by_status = {
        row[0]: int(row[1])
        for row in (
            await db.execute(
                select(Source.status, func.count()).group_by(Source.status)
            )
        ).all()
    }

    total_companies = int(
        await db.scalar(
            select(func.count()).select_from(Company).where(Company.is_active.is_(True))
        )
        or 0
    )
    with_sources = int(
        await db.scalar(
            select(func.count(func.distinct(Source.company_id))).where(
                Source.company_id.is_not(None), Source.status == "approved"
            )
        )
        or 0
    )

    return SourceStats(
        total_sources=sum(by_status.values()),
        by_type=by_type,
        by_status=by_status,
        companies_with_sources=with_sources,
        companies_without_sources=max(total_companies - with_sources, 0),
    )
