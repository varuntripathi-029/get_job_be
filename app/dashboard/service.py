"""Dashboard aggregates, with a process-local cache.

These are the landing page's queries, so they run on every anonymous visit and
several are joins across the whole companies table. The cache is an in-process
dict rather than Redis: Upstash bills per command, and paying one on every page
view for data that changes at crawl frequency would be the largest single line
in the budget.

Consequences, accepted deliberately: the cache is per-process, so two workers can
disagree for up to one TTL, and it empties on restart. For a single free-tier
instance serving public aggregates, neither matters.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.dashboard.schemas import (
    ActivityEvent,
    DashboardStats,
    IndustriesResponse,
    IndustryBreakdown,
    TrendingCompany,
)
from app.extraction.models import Event
from app.jobs.models import Job
from app.scoring.models import CompanyScore
from app.sources.models import Source

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[datetime, Any]] = {}


def cached(key: str, ttl_minutes: int) -> Callable:
    """Memoise an async function's result in process memory for `ttl_minutes`.

    The key is fixed per function, so anything whose result depends on arguments
    must incorporate them — see `trending_companies`, which caches by hand.
    """

    def decorator(func_: Callable) -> Callable:
        @functools.wraps(func_)
        async def wrapper(*args: object, **kwargs: object) -> Any:
            now = datetime.now(UTC)
            hit = _cache.get(key)
            if hit is not None and now < hit[0]:
                return hit[1]
            result = await func_(*args, **kwargs)
            _cache[key] = (now + timedelta(minutes=ttl_minutes), result)
            return result

        return wrapper

    return decorator


def clear_cache() -> None:
    """Drop every cached entry. Used by tests and after a manual reseed."""
    _cache.clear()


def _latest_scores():
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


@cached("stats", ttl_minutes=5)
async def get_stats(db: AsyncSession) -> DashboardStats:
    since = datetime.now(UTC) - timedelta(days=30)

    total_companies = await db.scalar(
        select(func.count()).select_from(Company).where(Company.is_active.is_(True))
    )
    total_active_jobs = await db.scalar(
        select(func.count()).select_from(Job).where(Job.is_active.is_(True))
    )
    total_events_30d = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.observed_at >= since,
            Event.is_canonical.is_(True),
            Event.status == "active",
        )
    )
    total_sources = await db.scalar(
        select(func.count()).select_from(Source).where(Source.status == "approved")
    )
    last_crawl_at = await db.scalar(select(func.max(Source.last_successful_crawl_at)))

    return DashboardStats(
        total_companies=int(total_companies or 0),
        total_active_jobs=int(total_active_jobs or 0),
        total_events_30d=int(total_events_30d or 0),
        total_sources=int(total_sources or 0),
        last_crawl_at=last_crawl_at,
    )


async def trending_companies(
    db: AsyncSession, *, limit: int = 10, industry: str | None = None
) -> list[TrendingCompany]:
    """Companies whose momentum rose most over the last 7 days.

    Cached by hand rather than with @cached because the result depends on the
    arguments, and a single fixed key would serve one filter's results for
    another's.
    """
    cache_key = f"trending:{limit}:{industry or '-'}"
    now = datetime.now(UTC)
    hit = _cache.get(cache_key)
    if hit is not None and now < hit[0]:
        return hit[1]

    since = now - timedelta(days=7)
    latest = _latest_scores()
    baseline = (
        select(CompanyScore.company_id, CompanyScore.momentum_score)
        .where(CompanyScore.scored_at < since)
        .distinct(CompanyScore.company_id)
        .order_by(CompanyScore.company_id, CompanyScore.scored_at.desc())
        .subquery("baseline_scores")
    )
    # Active job count per company, as a scalar subquery so companies with no
    # jobs still appear (a plain join would drop them).
    job_count = (
        select(func.count())
        .select_from(Job)
        .where(Job.company_id == Company.id, Job.is_active.is_(True))
        .scalar_subquery()
    )
    delta = (
        latest.c.momentum_score - func.coalesce(baseline.c.momentum_score, 0.0)
    ).label("delta")

    stmt = (
        select(
            Company.slug,
            Company.name,
            Company.industry,
            latest.c.momentum_score,
            latest.c.momentum_label,
            delta,
            job_count.label("active_jobs"),
        )
        .join(latest, latest.c.company_id == Company.id)
        .outerjoin(baseline, baseline.c.company_id == Company.id)
        .where(Company.is_active.is_(True))
        .order_by(delta.desc())
        .limit(limit)
    )
    if industry:
        stmt = stmt.where(Company.industry == industry)

    rows = (await db.execute(stmt)).all()

    # The headline signal per company, fetched in one pass rather than per row.
    slugs = [row.slug for row in rows]
    signals = await _top_signals(db, slugs) if slugs else {}

    trending = [
        TrendingCompany(
            slug=row.slug,
            name=row.name,
            industry=row.industry,
            momentum_score=float(row.momentum_score),
            momentum_label=row.momentum_label,
            score_delta=float(row.delta),
            top_signal=signals.get(row.slug),
            active_jobs=int(row.active_jobs or 0),
        )
        for row in rows
    ]

    _cache[cache_key] = (now + timedelta(minutes=10), trending)
    return trending


async def _top_signals(db: AsyncSession, slugs: list[str]) -> dict[str, str]:
    """Most recent event title per company slug."""
    stmt = (
        select(Company.slug, Event.title)
        .join(Event, Event.company_id == Company.id)
        .where(
            Company.slug.in_(slugs),
            Event.is_canonical.is_(True),
            Event.status == "active",
        )
        .distinct(Company.slug)
        .order_by(Company.slug, Event.observed_at.desc())
    )
    return {slug: title for slug, title in (await db.execute(stmt)).all()}


async def recent_activity(
    db: AsyncSession, *, limit: int = 20, event_type: str | None = None
) -> list[ActivityEvent]:
    """Live event feed. Not cached — freshness is the point."""
    stmt = (
        select(Event, Company.name, Company.slug)
        .join(Company, Company.id == Event.company_id)
        .where(Event.is_canonical.is_(True), Event.status == "active")
        .order_by(Event.observed_at.desc(), Event.id.desc())
        .limit(limit)
    )
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)

    return [
        ActivityEvent(
            id=event.id,
            event_type=event.event_type,
            title=event.title,
            company_name=name,
            company_slug=slug,
            observed_at=event.observed_at,
            event_occurred_at=event.event_occurred_at,
            source_count=event.source_count,
        )
        for event, name, slug in (await db.execute(stmt)).all()
    ]


@cached("industries", ttl_minutes=30)
async def industry_breakdown(db: AsyncSession) -> IndustriesResponse:
    latest = _latest_scores()
    stmt = (
        select(
            Company.industry,
            func.count(Company.id).label("count"),
            func.avg(latest.c.momentum_score).label("avg_score"),
        )
        .outerjoin(latest, latest.c.company_id == Company.id)
        .where(Company.is_active.is_(True), Company.industry.is_not(None))
        .group_by(Company.industry)
        .order_by(func.count(Company.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    return IndustriesResponse(
        industries=[
            IndustryBreakdown(
                name=industry,
                count=int(count),
                # None rather than 0.0 when no company in the industry has been
                # scored yet — zero would read as "scored, and bad".
                avg_score=round(float(avg), 1) if avg is not None else None,
            )
            for industry, count, avg in rows
        ]
    )
