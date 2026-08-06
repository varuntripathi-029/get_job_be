"""Admin read models — crawler health and instance metrics."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.companies.models import Company
from app.extraction.models import Event
from app.jobs.models import Job
from app.newsletter.models import NewsletterSubscriber
from app.sources.models import Source
from app.sources.schemas import CrawlerHealthRow


async def crawler_health(
    db: AsyncSession, *, limit: int = 200, only_failing: bool = False
) -> list[CrawlerHealthRow]:
    """Per-source crawl status, worst-offenders first."""
    stmt = (
        select(Source, Company.name)
        .outerjoin(Company, Source.company_id == Company.id)
        .where(Source.status == "approved")
        .order_by(Source.consecutive_failures.desc(), Source.next_crawl_at)
        .limit(limit)
    )
    if only_failing:
        stmt = stmt.where(Source.consecutive_failures > 0)

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
