"""Extraction services — LLM parsing and public event reads."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.common.pagination import PaginatedResponse, PaginationParams, paginate
from app.companies.models import Company
from app.config import settings
from app.extraction import llm
from app.extraction.models import Event
from app.extraction.prompts import classify_v1, extract_v1, resume_v1
from app.extraction.schemas import (
    ClassificationResult,
    EventResponse,
    ExtractedEvent,
    ParsedResume,
)

logger = logging.getLogger(__name__)


async def parse_resume_with_llm(text: str) -> tuple[ParsedResume, str | None]:
    """Extract structured fields from resume text using the classifier model.

    Returns `(parsed, model_id)`. Parsing is deliberately forgiving: a resume
    that yields only skills is more useful than an upload that fails, so a bad
    or missing response degrades to an empty `ParsedResume` rather than raising.
    """
    result = await llm.complete_json(
        provider=settings.classifier_provider,
        model=settings.classifier_model,
        system_prompt=resume_v1.SYSTEM_PROMPT,
        user_content=text,
        max_input_chars=resume_v1.MAX_INPUT_CHARS,
    )
    if result is None:
        logger.warning("resume parse returned nothing; storing raw text only")
        return ParsedResume(), None

    try:
        parsed = ParsedResume.model_validate(result.data)
    except Exception as exc:  # noqa: BLE001 — partial data beats no data
        logger.warning("resume parse failed validation (%s): %s", exc, result.data)
        parsed = _salvage(result.data)

    logger.info(
        "parsed resume via %s/%s in %sms — %d skills, families=%s, seniority=%s",
        result.provider,
        result.model,
        result.latency_ms,
        len(parsed.skills),
        parsed.role_families,
        parsed.seniority,
    )
    return parsed, result.model


def _salvage(data: dict) -> ParsedResume:
    """Rebuild a ParsedResume field by field, discarding only what is broken."""
    salvaged = ParsedResume()
    for field in ParsedResume.model_fields:
        if field not in data:
            continue
        try:
            salvaged = salvaged.model_copy(
                update=ParsedResume.model_validate(
                    {field: data[field]}
                ).model_dump(include={field})
            )
        except Exception:  # noqa: BLE001, S110 — this field is simply unusable
            continue
    return salvaged


def _public_events_stmt() -> Select:
    """Canonical, active events joined to their company.

    Non-canonical rows are duplicates already merged into a canonical event, and
    retracted rows are signals we no longer stand behind. Neither should ever
    reach a public endpoint.
    """
    return (
        select(Event, Company.name, Company.slug)
        .join(Company, Company.id == Event.company_id)
        .where(Event.is_canonical.is_(True), Event.status == "active")
    )


EVENT_SORTS = ("observed_at", "event_occurred_at")


async def list_events(
    db: AsyncSession,
    params: PaginationParams,
    *,
    event_type: str | None = None,
    company_slug: str | None = None,
    days: int | None = None,
    sort_by: str = "observed_at",
    sort_order: str = "desc",
) -> PaginatedResponse[EventResponse]:
    if sort_by not in EVENT_SORTS:
        raise ValidationError(
            f"sort_by must be one of {', '.join(EVENT_SORTS)}, got {sort_by!r}."
        )

    stmt = _public_events_stmt()
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    if company_slug:
        stmt = stmt.where(Company.slug == company_slug)
    if days is not None:
        # Filters on observed_at even when sorting by event_occurred_at: "last
        # N days" means what we learned recently, and event_occurred_at is null
        # for plenty of events.
        stmt = stmt.where(
            Event.observed_at >= datetime.now(UTC) - timedelta(days=days)
        )

    column = getattr(Event, sort_by)
    order = column.desc() if sort_order == "desc" else column.asc()
    # Tiebreak on id so pages don't reshuffle when timestamps collide.
    stmt = stmt.order_by(order.nullslast(), Event.id.desc())

    return await paginate(db, stmt, params, _to_response)


async def recent_events_for_company(
    db: AsyncSession, company_id, *, limit: int = 5
) -> list[EventResponse]:
    result = await db.execute(
        _public_events_stmt()
        .where(Event.company_id == company_id)
        .order_by(Event.observed_at.desc(), Event.id.desc())
        .limit(limit)
    )
    return [_to_response(row) for row in result.all()]


def _to_response(row) -> EventResponse:
    event, company_name, company_slug = row
    payload = EventResponse.model_validate(event)
    payload.company_name = company_name
    payload.company_slug = company_slug
    return payload


MIN_EXTRACTION_CONFIDENCE = 0.3


async def classify_content(text: str) -> ClassificationResult:
    """Cheap relevance gate before the expensive extractor.

    Fails open. A classifier outage or an unparseable response returns
    `is_relevant=True`, because dropping a real signal is permanent while an
    unnecessary extraction costs one call.
    """
    result = await llm.complete_json(
        provider=settings.classifier_provider,
        model=settings.classifier_model,
        system_prompt=classify_v1.SYSTEM_PROMPT,
        user_content=text,
        max_input_chars=classify_v1.MAX_INPUT_CHARS,
    )
    if result is None:
        logger.warning("classifier unavailable — passing content through")
        return ClassificationResult(is_relevant=True, reason="classifier unavailable")

    data = result.data
    is_relevant = data.get("is_relevant")
    if not isinstance(is_relevant, bool):
        logger.warning("classifier returned no boolean verdict: %s", data)
        is_relevant = True

    logger.info(
        "classify: relevant=%s via %s in %sms (in=%s out=%s) — %s",
        is_relevant,
        result.model,
        result.latency_ms,
        result.prompt_tokens,
        result.completion_tokens,
        str(data.get("reason", ""))[:120],
    )
    return ClassificationResult(
        is_relevant=is_relevant,
        reason=str(data.get("reason") or ""),
        model=result.model,
        latency_ms=result.latency_ms,
    )


async def extract_events(text: str, source_url: str) -> list[ExtractedEvent]:
    """Pull structured events out of content the classifier accepted.

    Returns an empty list on any failure — extraction is best-effort across
    thousands of pages and one bad response must not fail a crawl.
    """
    result = await llm.complete_json(
        provider=settings.extractor_provider,
        model=settings.extractor_model,
        system_prompt=extract_v1.SYSTEM_PROMPT,
        user_content=text,
        max_input_chars=extract_v1.MAX_INPUT_CHARS,
    )
    if result is None:
        logger.warning("extractor returned nothing for %s", source_url)
        return []

    # The prompt asks for {"events": [...]} rather than a bare array because the
    # client requests response_format=json_object, which cannot return a
    # top-level array. Tolerate a few shapes anyway.
    raw = result.data.get("events")
    if raw is None:
        for key in ("data", "results", "items"):
            if isinstance(result.data.get(key), list):
                raw = result.data[key]
                break
    if not isinstance(raw, list):
        logger.warning(
            "extractor response had no event list for %s: %s",
            source_url,
            list(result.data)[:5],
        )
        return []

    events: list[ExtractedEvent] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            event = ExtractedEvent.model_validate(item)
        except Exception as exc:  # noqa: BLE001 — skip the bad one, keep the rest
            logger.debug("discarded malformed event from %s: %s", source_url, exc)
            continue
        if event.confidence < MIN_EXTRACTION_CONFIDENCE:
            logger.debug(
                "discarded low-confidence event %.2f: %s", event.confidence, event.title
            )
            continue
        events.append(event)

    logger.info(
        "extract: %d events from %s via %s in %sms (in=%s out=%s)",
        len(events),
        source_url,
        result.model,
        result.latency_ms,
        result.prompt_tokens,
        result.completion_tokens,
    )
    return events
