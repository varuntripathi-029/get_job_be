"""Weekly newsletter content assembly."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.extraction.models import Event
from app.jobs.models import Job
from app.newsletter.schemas import (
    CompanyEntry,
    EventEntry,
    HotspotEntry,
    MoverEntry,
    NewsletterContent,
)
from app.newsletter.templates import format_date
from app.scoring.models import CompanyScore

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
MOVERS_LIMIT = 10
HOTSPOTS_LIMIT = 10
EVENTS_LIMIT = 15
ENTRANTS_LIMIT = 10


async def _top_movers(db: AsyncSession, since: datetime) -> list[MoverEntry]:
    """Companies whose momentum rose most over the window.

    Written as two DISTINCT ON scans rather than the correlated-subquery form:
    joining every historical score older than the window multiplies rows per
    company and inflates the ranking. DISTINCT ON collapses each side to exactly
    one row per company first, so the join is 1:1 and the delta is well defined.
    """
    latest = (
        select(
            CompanyScore.company_id,
            CompanyScore.momentum_score,
            CompanyScore.momentum_label,
        )
        .distinct(CompanyScore.company_id)
        .order_by(CompanyScore.company_id, CompanyScore.scored_at.desc())
        .subquery("latest")
    )
    # Most recent score at or before the window start — the baseline to compare to.
    baseline = (
        select(CompanyScore.company_id, CompanyScore.momentum_score)
        .where(CompanyScore.scored_at < since)
        .distinct(CompanyScore.company_id)
        .order_by(CompanyScore.company_id, CompanyScore.scored_at.desc())
        .subquery("baseline")
    )

    delta = (
        latest.c.momentum_score - func.coalesce(baseline.c.momentum_score, 0.0)
    ).label("delta")
    result = await db.execute(
        select(Company.name, Company.slug, latest.c.momentum_score,
               latest.c.momentum_label, delta)
        .join(latest, latest.c.company_id == Company.id)
        .outerjoin(baseline, baseline.c.company_id == Company.id)
        .where(Company.is_active.is_(True), delta > 0)
        .order_by(delta.desc())
        .limit(MOVERS_LIMIT)
    )
    return [
        MoverEntry(
            name=name,
            slug=slug,
            momentum_score=float(score),
            momentum_label=label,
            delta=float(d),
        )
        for name, slug, score, label, d in result.all()
    ]


async def _hiring_hotspots(db: AsyncSession, since: datetime) -> list[HotspotEntry]:
    """Companies that opened the most roles in the window."""
    new_jobs = func.count(Job.id).label("new_jobs")
    result = await db.execute(
        select(Company.name, Company.slug, new_jobs)
        .join(Job, Job.company_id == Company.id)
        .where(Job.first_seen_at >= since, Job.is_active.is_(True))
        .group_by(Company.id, Company.name, Company.slug)
        .order_by(new_jobs.desc())
        .limit(HOTSPOTS_LIMIT)
    )
    return [
        HotspotEntry(name=name, slug=slug, new_jobs=int(count))
        for name, slug, count in result.all()
    ]


async def _notable_events(db: AsyncSession, since: datetime) -> list[EventEntry]:
    result = await db.execute(
        select(Event, Company.name, Company.slug)
        .join(Company, Company.id == Event.company_id)
        .where(
            Event.observed_at >= since,
            Event.is_canonical.is_(True),
            Event.status == "active",
        )
        .order_by(Event.observed_at.desc())
        .limit(EVENTS_LIMIT)
    )
    entries = []
    for event, company_name, company_slug in result.all():
        # Evidence is [{source_url, excerpt, ...}]; link the first entry so the
        # claim in the email is one click from its source.
        evidence_url = None
        if isinstance(event.evidence, list) and event.evidence:
            first = event.evidence[0]
            if isinstance(first, dict):
                evidence_url = first.get("source_url")
        entries.append(
            EventEntry(
                company_name=company_name,
                company_slug=company_slug,
                event_type=event.event_type,
                title=event.title,
                occurred_at=event.event_occurred_at or event.observed_at,
                evidence_url=evidence_url,
            )
        )
    return entries


async def _new_entrants(db: AsyncSession, since: datetime) -> list[CompanyEntry]:
    result = await db.execute(
        select(Company.name, Company.slug, Company.industry)
        .where(Company.created_at >= since, Company.is_active.is_(True))
        .order_by(Company.created_at.desc())
        .limit(ENTRANTS_LIMIT)
    )
    return [
        CompanyEntry(name=name, slug=slug, industry=industry)
        for name, slug, industry in result.all()
    ]


async def generate_newsletter(
    db: AsyncSession, *, edition_number: int = 1
) -> NewsletterContent:
    """Assemble this week's digest from the last 7 days of stored data."""
    now = datetime.now(UTC)
    since = now - timedelta(days=WINDOW_DAYS)

    content = NewsletterContent(
        subject=f"HireSignal Weekly — {format_date(now)}",
        top_movers=await _top_movers(db, since),
        hiring_hotspots=await _hiring_hotspots(db, since),
        notable_events=await _notable_events(db, since),
        new_entrants=await _new_entrants(db, since),
        generated_at=now,
        edition_number=edition_number,
    )

    logger.info(
        "newsletter #%d: %d movers, %d hotspots, %d events, %d new companies",
        edition_number,
        len(content.top_movers),
        len(content.hiring_hotspots),
        len(content.notable_events),
        len(content.new_entrants),
    )
    return content
