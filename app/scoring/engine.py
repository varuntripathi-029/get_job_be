"""Deterministic momentum scoring.

A pure function of stored events: the same events always produce the same
number. No LLM touches the score — models produce the events, arithmetic
produces the score. That is what makes it explainable, reproducible, and
defensible when someone asks why a company is at 78.

Scores are append-only. Recomputing writes a new row so any score ever shown to
a user can still be reproduced alongside the events that caused it.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.extraction.models import Event
from app.scoring.models import CompanyScore

logger = logging.getLogger(__name__)

SCORE_VERSION = "v1"

# How much each signal is worth at the moment it happens. Funding leads because
# it is the strongest public predictor of headcount growth; a layoff subtracts.
BASE_WEIGHTS = {
    "funding": 25,
    "new_office": 20,
    "leadership_change": 18,
    "engineering_expansion": 15,
    "ai_division": 15,
    "infrastructure_investment": 10,
    "product_launch": 8,
    "acquisition": 12,
    "partnership": 5,
    "career_page_update": 8,
    "layoff": -20,
}

# Days for a signal to lose half its weight. Tuned to how long each kind of
# event stays predictive: a funding round still means something four months on,
# a career-page change is stale within a fortnight.
HALF_LIVES = {
    "funding": 120,
    "new_office": 90,
    "leadership_change": 90,
    "engineering_expansion": 60,
    "ai_division": 90,
    "infrastructure_investment": 60,
    "product_launch": 45,
    "acquisition": 90,
    "partnership": 30,
    "career_page_update": 14,
    "layoff": 90,
}

TIER_FACTORS = {
    "first_party": 1.0,
    "tier1_press": 0.9,
    "aggregator": 0.7,
    "blog": 0.5,
    "rumor": 0.3,
}

TIER1_PRESS_DOMAINS = (
    "techcrunch.com", "yourstory.com", "inc42.com", "entrackr.com",
    "economictimes.indiatimes.com", "livemint.com", "moneycontrol.com",
    "business-standard.com", "thehindubusinessline.com", "reuters.com",
    "bloomberg.com", "forbes.com", "vccircle.com",
)

AGGREGATOR_MARKERS = ("newsapi", "gnews", "serpapi", "news.google", "bing.com")

# An ATS board is first-party evidence even though the domain belongs to the
# vendor: only the company itself can post to its own board, so a job appearing
# there is as authoritative as its own careers page.
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
             "recruitee.com", "breezy.hr", "freshteam.com", "pinpointhq.com")

# Only the last 180 days count. Beyond that, decay has reduced every weight to
# noise and the query would scan history for nothing.
LOOKBACK_DAYS = 180

# Recent activity required for a company to be more than dormant.
RECENT_ACTIVITY_DAYS = 30

# Sigmoid parameters: midpoint of the curve, and how fast it saturates.
SIGMOID_MIDPOINT = 50.0
SIGMOID_STEEPNESS = 20.0


def classify_source_tier(url: str | None, company_domain: str | None) -> str:
    """Rank a piece of evidence by how much its origin can be trusted.

    A company announcing its own funding is first-party fact. The same claim on
    an unknown blog is a rumour, and should not move a score as much.
    """
    if not url:
        return "rumor"

    host = (urlparse(url).hostname or url).lower()
    if host.startswith("www."):
        host = host[4:]

    if company_domain and company_domain.lower() in host:
        return "first_party"
    if any(ats in host for ats in ATS_HOSTS):
        return "first_party"
    if any(domain in host for domain in TIER1_PRESS_DOMAINS):
        return "tier1_press"
    if any(marker in host or marker in url.lower() for marker in AGGREGATOR_MARKERS):
        return "aggregator"
    if host:
        return "blog"
    return "rumor"


def best_tier(event: Event, company_domain: str | None) -> str:
    """The strongest tier among an event's evidence.

    Best, not average: one first-party confirmation makes a claim solid however
    many aggregators also repeated it.
    """
    tiers = [
        classify_source_tier(item.get("source_url"), company_domain)
        for item in (event.evidence or [])
        if isinstance(item, dict)
    ]
    if not tiers:
        return "rumor"
    return max(tiers, key=lambda tier: TIER_FACTORS.get(tier, 0.0))


def event_weight(event: Event, company_domain: str | None, now: datetime) -> float:
    """One event's contribution to the raw score."""
    base = BASE_WEIGHTS.get(event.event_type, 0)
    if base == 0:
        return 0.0

    occurred = event.event_occurred_at or event.observed_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    # Clamped at zero: a source claiming a future date must not amplify a signal
    # via negative age.
    age_days = max((now - occurred).days, 0)

    half_life = HALF_LIVES.get(event.event_type, 90)
    decay = 0.5 ** (age_days / half_life)

    # Corroboration: one source earns 70% of the weight, five or more earn all
    # of it. Independent confirmation is evidence, but with diminishing returns.
    corroboration = min(event.source_count or 1, 5) / 5
    corroboration_factor = 0.7 + 0.3 * corroboration

    tier_factor = TIER_FACTORS.get(best_tier(event, company_domain), 0.3)

    return base * decay * corroboration_factor * tier_factor


def normalize(raw_score: float) -> float:
    """Squash an unbounded weight sum into 0-100.

    A sigmoid rather than a linear clamp so the busiest companies stay
    distinguishable instead of all pinning at 100.
    """
    momentum = 100 / (1 + math.exp(-(raw_score - SIGMOID_MIDPOINT) / SIGMOID_STEEPNESS))
    return max(0.0, min(100.0, momentum))


def label_for(momentum_score: float, has_recent_activity: bool) -> str:
    # A company with an old funding round and nothing since is dormant, not
    # "low momentum" — the distinction matters to a job seeker.
    if momentum_score < 10 and not has_recent_activity:
        return "none"
    if momentum_score < 25:
        return "low"
    if momentum_score < 50:
        return "moderate"
    if momentum_score < 75:
        return "high"
    return "very_high"


async def compute_score(
    db: AsyncSession, company_id: uuid.UUID, *, commit: bool = True
) -> CompanyScore:
    """Score a company from its stored events and append the result."""
    # The session is autoflush=False, and callers routinely add events and then
    # score in the same transaction. Without an explicit flush the SELECT below
    # cannot see those events and every freshly crawled company scores 0.
    await db.flush()

    now = datetime.now(UTC)
    since = now - timedelta(days=LOOKBACK_DAYS)

    company = await db.get(Company, company_id)
    company_domain = company.canonical_domain if company else None

    events = list(
        (
            await db.execute(
                select(Event).where(
                    Event.company_id == company_id,
                    Event.is_canonical.is_(True),
                    Event.status == "active",
                    func.coalesce(Event.event_occurred_at, Event.observed_at)
                    >= since,
                )
            )
        )
        .scalars()
        .all()
    )

    previous = await db.scalar(
        select(CompanyScore)
        .where(CompanyScore.company_id == company_id)
        .order_by(CompanyScore.scored_at.desc())
        .limit(1)
    )

    if not events:
        score = _persist(
            db,
            company_id,
            momentum_score=0.0,
            momentum_label="none",
            event_ids=[],
            previous=previous,
            signal_strength=0.0,
        )
    else:
        weights = [event_weight(e, company_domain, now) for e in events]
        raw_score = sum(weights)
        momentum = normalize(raw_score)

        recent_cutoff = now - timedelta(days=RECENT_ACTIVITY_DAYS)
        has_recent = any(
            (e.event_occurred_at or e.observed_at).replace(
                tzinfo=(e.event_occurred_at or e.observed_at).tzinfo or UTC
            )
            >= recent_cutoff
            for e in events
        )

        score = _persist(
            db,
            company_id,
            momentum_score=round(momentum, 2),
            momentum_label=label_for(momentum, has_recent),
            event_ids=[e.id for e in events],
            previous=previous,
            signal_strength=round(raw_score, 2),
        )
        logger.info(
            "scored %s: raw=%.1f momentum=%.1f (%s) from %d events",
            company_id,
            raw_score,
            momentum,
            score.momentum_label,
            len(events),
        )

    if commit:
        await db.commit()
        await db.refresh(score)
    return score


def _persist(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    momentum_score: float,
    momentum_label: str,
    event_ids: list[uuid.UUID],
    previous: CompanyScore | None,
    signal_strength: float,
) -> CompanyScore:
    # None, not momentum_score - 0, when there is no prior score. A first score
    # is a baseline, not a rise; /dashboard/trending ranks by delta, so treating
    # it as a full-size gain would bury real movers under every newly tracked
    # company.
    delta = (
        round(momentum_score - previous.momentum_score, 2)
        if previous is not None
        else None
    )

    score = CompanyScore(
        company_id=company_id,
        score_version=SCORE_VERSION,
        momentum_score=momentum_score,
        momentum_label=momentum_label,
        signal_strength=signal_strength,
        contributing_event_ids=event_ids,
        score_delta=delta,
        scored_at=datetime.now(UTC),
    )
    db.add(score)
    return score
