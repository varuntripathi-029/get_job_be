"""Admin read models — crawler health and instance metrics."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.companies.models import Company
from app.extraction.models import Event
from app.jobs.models import Job
from app.newsletter.models import NewsletterSubscriber
from app.scoring.models import CompanyScore
from app.sources.models import Source
from app.sources.schemas import CrawlerHealthRow


async def crawler_health(
    db: AsyncSession,
    *,
    limit: int = 200,
    only_failing: bool = False,
    search: str | None = None,
) -> list[CrawlerHealthRow]:
    """Per-source crawl status, worst-offenders first.

    `search` matches the URL or the attached company's name, so a specific
    source can be found without paging past the limit.
    """
    stmt = (
        select(Source, Company.name)
        .outerjoin(Company, Source.company_id == Company.id)
        # Disabled sources are shown too so they can be re-enabled or deleted
        # from here; pending and rejected live in the review queue instead.
        .where(Source.status.in_(("approved", "disabled")))
        .order_by(Source.consecutive_failures.desc(), Source.next_crawl_at)
        .limit(limit)
    )
    if only_failing:
        stmt = stmt.where(Source.consecutive_failures > 0)
    if search and search.strip():
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Source.url).like(pattern),
                func.lower(Company.name).like(pattern),
            )
        )

    result = await db.execute(stmt)
    return [
        CrawlerHealthRow(
            source_id=source.id,
            url=source.url,
            company_name=company_name,
            source_type=source.source_type,
            fetch_tier=source.fetch_tier,
            status=source.status,
            last_crawl_at=source.last_crawl_at,
            last_successful_crawl_at=source.last_successful_crawl_at,
            next_crawl_at=source.next_crawl_at,
            consecutive_failures=source.consecutive_failures,
            last_failure_reason=source.last_failure_reason,
            content_hash=source.content_hash,
            total_crawls=source.total_crawls,
            total_events_extracted=source.total_events_extracted,
        )
        for source, company_name in result.all()
    ]


async def _count(db: AsyncSession, model: type, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return int(await db.scalar(stmt) or 0)


async def instance_metrics(db: AsyncSession) -> dict[str, object]:
    """Counts for the admin dashboard."""
    sources_by_status = {
        row.status: row.count
        for row in (
            await db.execute(
                select(Source.status, func.count().label("count")).group_by(
                    Source.status
                )
            )
        ).all()
    }

    return {
        "total_companies": await _count(db, Company),
        "active_companies": await _count(db, Company, Company.is_active.is_(True)),
        "sources_by_status": sources_by_status,
        "total_sources": sum(sources_by_status.values()),
        "total_events": await _count(db, Event, Event.is_canonical.is_(True)),
        "active_jobs": await _count(db, Job, Job.is_active.is_(True)),
        "closed_jobs": await _count(db, Job, Job.is_active.is_(False)),
        "total_users": await _count(db, User),
        "total_subscribers": await _count(
            db, NewsletterSubscriber, NewsletterSubscriber.is_active.is_(True)
        ),
    }


async def weekly_stats(db: AsyncSession) -> dict[str, object]:
    """Activity over the last 7 days, for the admin dashboard."""
    since = datetime.now(UTC) - timedelta(days=7)

    return {
        "window_days": 7,
        "since": since.isoformat(),
        "new_companies": await _count(db, Company, Company.created_at >= since),
        "new_sources": await _count(db, Source, Source.created_at >= since),
        "new_events": await _count(
            db,
            Event,
            Event.observed_at >= since,
            Event.is_canonical.is_(True),
            Event.status == "active",
        ),
        "new_jobs": await _count(db, Job, Job.first_seen_at >= since),
        "closed_jobs": await _count(db, Job, Job.closed_at >= since),
        "new_subscribers": await _count(
            db, NewsletterSubscriber, NewsletterSubscriber.created_at >= since
        ),
        "confirmed_subscribers": await _count(
            db,
            NewsletterSubscriber,
            NewsletterSubscriber.confirmed_at >= since,
        ),
        "scores_computed": await _count(
            db, CompanyScore, CompanyScore.scored_at >= since
        ),
        # Drives the "matching is degraded" banner: jobs with no vector are
        # invisible to resume matching.
        "jobs_missing_embedding": await _count(
            db, Job, Job.is_active.is_(True), Job.embedding.is_(None)
        ),
    }
