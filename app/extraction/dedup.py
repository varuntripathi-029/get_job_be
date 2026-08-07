"""Event deduplication.

The same funding round reaches us from the company blog, TechCrunch, NewsAPI and
GNews. Without this, that is four events and four times the score. With it, it is
one canonical event carrying four pieces of evidence — which is also what makes
`source_count` a meaningful corroboration signal in the scoring engine.

Two stages, as in classic record linkage: a cheap blocking query narrows
candidates to the same company, type and time window, then a per-type comparison
decides identity within that block.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.models import Event
from app.extraction.schemas import ExtractedEvent

logger = logging.getLogger(__name__)

# How far apart two reports of the same happening can be. Press coverage of a
# funding round routinely trails the announcement by a week or more.
WINDOW_DAYS = 14

# Fraction of shared words above which two titles describe the same thing.
TITLE_OVERLAP_THRESHOLD = 0.6

# Funding amounts differ across reports (pre/post-money, currency conversion),
# so exact equality would never match.
AMOUNT_TOLERANCE = 0.2

# Words carrying no identifying information, removed before comparing titles.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
        "its", "as", "with", "from", "by", "is", "are", "was", "were", "has",
        "have", "new", "million", "billion", "crore", "usd", "inr",
    }
)


def _tokens(title: str) -> set[str]:
    return {
        word
        for word in "".join(
            ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in title
        ).split()
        if word and word not in _STOPWORDS
    }


def title_overlap(a: str, b: str) -> float:
    """Jaccard-style overlap over the larger title, ignoring stopwords."""
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b), 1)


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def _amounts_match(a: object, b: object) -> bool:
    """True when both are absent, or within tolerance of each other."""
    if a is None or b is None:
        # One side not reporting an amount is not evidence of a different round.
        return True
    try:
        left, right = float(a), float(b)
    except (TypeError, ValueError):
        return True
    if left <= 0 or right <= 0:
        return True
    return abs(left - right) / max(left, right) <= AMOUNT_TOLERANCE


def is_same_event(extracted: ExtractedEvent, existing: Event) -> bool:
    """Whether an extracted event describes the same happening as a stored one.

    Comparison is per type because what makes two reports identical differs:
    a funding round is identified by its round and size, a leadership change by
    the person, an office by the city.
    """
    new_data = extracted.structured_data or {}
    old_data = existing.structured_data or {}

    if extracted.event_type == "funding":
        rounds_match = _norm(new_data.get("round")) == _norm(old_data.get("round"))
        return rounds_match and _amounts_match(
            new_data.get("amount_usd"), old_data.get("amount_usd")
        )

    if extracted.event_type == "leadership_change":
        person = _norm(new_data.get("person"))
        if not person or person != _norm(old_data.get("person")):
            return False
        # Same person, same week, differing role strings ("VP Eng" vs "VP of
        # Engineering") is still one hire.
        new_role, old_role = _norm(new_data.get("role")), _norm(old_data.get("role"))
        return not new_role or not old_role or title_overlap(new_role, old_role) > 0.5

    if extracted.event_type == "new_office":
        city = _norm(new_data.get("city"))
        return bool(city) and city == _norm(old_data.get("city"))

    if extracted.event_type == "acquisition":
        target = _norm(new_data.get("target"))
        return bool(target) and target == _norm(old_data.get("target"))

    return title_overlap(extracted.title, existing.title) > TITLE_OVERLAP_THRESHOLD


def _effective_date():
    """When an event happened, falling back to when we saw it.

    func.coalesce, not Column.coalesce — the latter is not a SQLAlchemy
    column method and raises at query build time.
    """
    return func.coalesce(Event.event_occurred_at, Event.observed_at)


async def find_duplicate(
    db: AsyncSession, extracted: ExtractedEvent, company_id: uuid.UUID
) -> Event | None:
    """Blocking query plus per-type comparison."""
    occurred = extracted.event_occurred_at or datetime.now(UTC)
    window_start = occurred - timedelta(days=WINDOW_DAYS)
    window_end = occurred + timedelta(days=WINDOW_DAYS)

    # Blocking is done in SQL on indexed columns; the date comparison uses
    # COALESCE so events with no known occurrence date fall back to when we saw
    # them, matching how scoring reads them.
    candidates = (
        await db.execute(
            select(Event).where(
                Event.company_id == company_id,
                Event.event_type == extracted.event_type,
                Event.is_canonical.is_(True),
                Event.status == "active",
                _effective_date() >= window_start,
                _effective_date() <= window_end,
            )
        )
    ).scalars().all()

    for candidate in candidates:
        if is_same_event(extracted, candidate):
            return candidate
    return None


def build_evidence(extracted: ExtractedEvent, source_url: str) -> dict:
    return {
        "source_url": source_url,
        "excerpt": extracted.evidence_excerpt,
        "published_at": (
            extracted.event_occurred_at.isoformat()
            if extracted.event_occurred_at
            else None
        ),
        "observed_at": datetime.now(UTC).isoformat(),
    }


async def deduplicate_event(
    db: AsyncSession,
    extracted: ExtractedEvent,
    company_id: uuid.UUID,
    source_url: str,
    *,
    extraction_model: str | None = None,
    prompt_version: str | None = None,
) -> tuple[Event, bool]:
    """Merge into an existing event or create a new one.

    Returns `(event, created)`. The caller uses `created` to decide whether the
    crawl actually produced a new signal.
    """
    evidence = build_evidence(extracted, source_url)
    existing = await find_duplicate(db, extracted, company_id)

    if existing is not None:
        # Same story from the same page twice adds no corroboration.
        already_seen = any(
            isinstance(item, dict) and item.get("source_url") == source_url
            for item in (existing.evidence or [])
        )
        if already_seen:
            logger.debug(
                "evidence from %s already attached to %s", source_url, existing.id
            )
            return existing, False

        # Reassigned rather than mutated in place: SQLAlchemy does not track
        # in-place edits to a JSONB list, so appending would never persist.
        existing.evidence = [*(existing.evidence or []), evidence]
        existing.source_count = (existing.source_count or 0) + 1
        if extracted.confidence > (existing.extraction_confidence or 0):
            existing.extraction_confidence = extracted.confidence

        logger.info(
            "merged evidence into event %s (%s), now %d sources",
            existing.id,
            existing.event_type,
            existing.source_count,
        )
        return existing, False

    event = Event(
        company_id=company_id,
        event_type=extracted.event_type,
        title=extracted.title,
        event_occurred_at=extracted.event_occurred_at,
        observed_at=datetime.now(UTC),
        structured_data=extracted.structured_data or {},
        evidence=[evidence],
        source_count=1,
        extraction_confidence=extracted.confidence,
        extraction_model=extraction_model,
        extraction_prompt_version=prompt_version,
        is_canonical=True,
        status="active",
    )
    db.add(event)
    logger.info(
        "new %s event for company %s: %s",
        event.event_type,
        company_id,
        event.title,
    )
    return event, True
