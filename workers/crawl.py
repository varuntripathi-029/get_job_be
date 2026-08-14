"""The crawl pipeline: scheduler tick and per-source crawl.

The database is the scheduler. `sources.next_crawl_at` is the only queue, so
there is no in-memory schedule to lose on restart and no separate scheduler
service to run. Beat only wakes up and asks "what is due?".
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import SSRFError
from app.companies.matcher import find_companies_in_text, resolve_event_subject
from app.companies.models import Company
from app.config import settings
from app.crawler.fetchers.base import FetchResult
from app.crawler.fetchers.browser import PlaywrightFetcher
from app.crawler.fetchers.html_text import html_to_linked_text
from app.crawler.fetchers.news_api import NewsAPIFetcher, NewsArticle, QuotaTracker
from app.crawler.fetchers.rss import RSSFetcher
from app.crawler.fetchers.static import StaticFetcher
from app.crawler.models import CrawlLog
from app.crawler.prefilter import is_relevant
from app.crawler.rate_limiter import build_rate_limiter
from app.crawler.ssrf import HostResolutionError, validate_url
from app.extraction.dedup import deduplicate_event
from app.extraction.prompts import extract_v1
from app.extraction.schemas import ExtractedEvent
from app.extraction.service import classify_content, extract_events
from app.jobs.careers import extract_career_page_jobs
from app.jobs.sync import reconcile_jobs, sync_company_jobs
from app.scoring.engine import compute_score
from app.sources.models import Source
from app.sources.service import discover_ats_board, register_discovered_board
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DISPATCH_BATCH = 10
MAX_BACKOFF_MINUTES = 10080  # 7 days
DISABLE_AFTER_FAILURES = 10
# Parked far enough ahead that the tick cannot re-dispatch a source while its
# task is still queued. The task overwrites this with a real time when it ends.
DISPATCH_LEASE_MINUTES = 60

NEWS_TIERS = ("news_api", "search_api")
NEWS_COMPANY_BATCH = 15


# --- scheduling helpers ------------------------------------------------------


def _schedule_success(source: Source, *, changed: bool) -> None:
    minutes = source.crawl_frequency_minutes
    # A page that did not change earns a longer leash. Over several quiet
    # cycles this meaningfully cuts wasted fetches on dormant sources.
    if not changed:
        minutes = int(minutes * 1.5)
    source.next_crawl_at = datetime.now(UTC) + timedelta(minutes=minutes)
    source.consecutive_failures = 0
    source.last_failure_reason = None


def _schedule_failure(source: Source, reason: str) -> None:
    source.consecutive_failures = (source.consecutive_failures or 0) + 1
    source.last_failure_reason = reason[:500]
    backoff = min(
        source.crawl_frequency_minutes * (2**source.consecutive_failures),
        MAX_BACKOFF_MINUTES,
    )
    source.next_crawl_at = datetime.now(UTC) + timedelta(minutes=backoff)

    if source.consecutive_failures >= DISABLE_AFTER_FAILURES:
        source.status = "disabled"
        logger.warning(
            "disabling source %s after %d consecutive failures: %s",
            source.url,
            source.consecutive_failures,
            reason,
        )


def _log(
    db: AsyncSession,
    source: Source,
    status: str,
    *,
    result: FetchResult | None = None,
    events: int = 0,
    error: str | None = None,
    text: str | None = None,
    changed: bool | None = None,
) -> None:
    db.add(
        CrawlLog(
            source_id=source.id,
            status=status,
            content_hash=result.content_hash if result else None,
            content_changed=changed,
            # Truncated: crawl logs are retained for 10 days across every source
            # and full page text would dominate the database.
            cleaned_text=(text or "")[:5000] or None,
            http_status_code=result.http_status if result else None,
            events_extracted=events,
            error_message=error[:1000] if error else None,
            duration_ms=result.duration_ms if result else None,
        )
    )


# --- extraction shared by every content tier ---------------------------------


async def _extract_and_store(
    db: AsyncSession,
    text: str,
    company_id: uuid.UUID | None,
    source_url: str,
    *,
    route_per_event: bool = False,
) -> tuple[int, set[uuid.UUID]]:
    """Classify, extract, dedup.

    Returns how many new events were created and which companies they landed
    on, so the caller can rescore exactly those.

    `route_per_event` is for documents with no company attached. A company blog
    is about one company and every event in it belongs to that company, but a
    news feed is not: one Entrackr piece can list eight departures across three
    firms. Attributing all of them to whichever company the article happened to
    resolve to is how a roundup of moves at one investor gets filed as evidence
    against an unrelated payments company — which is precisely the claim this
    product must never make. So each event is attributed by its own words, and
    an event that names nobody we track is dropped rather than guessed at.
    """
    classification = await classify_content(text)
    if not classification.is_relevant:
        logger.info("classifier rejected %s: %s", source_url, classification.reason)
        return 0, set()

    extracted = await extract_events(text, source_url)
    created = 0
    touched: set[uuid.UUID] = set()

    for event in extracted:
        target = company_id
        if route_per_event:
            owner = await resolve_event_subject(
                db, event.title, event.evidence_excerpt
            )
            if owner is None:
                logger.debug("no company in event %r — dropping", event.title[:80])
                continue
            target = owner.id
        if target is None:
            continue

        _, is_new = await deduplicate_event(
            db,
            event,
            target,
            source_url,
            extraction_model=settings.extractor_model,
            prompt_version=extract_v1.VERSION,
        )
        created += int(is_new)
        touched.add(target)

    return created, touched


# --- per-tier handlers -------------------------------------------------------


async def _crawl_ats(db: AsyncSession, source: Source) -> tuple[int, str]:
    company = await db.get(Company, source.company_id)
    if company is None:
        raise ValueError("ATS source has no company attached")

    result = await sync_company_jobs(db, company, source)
    events = 0

    # New postings are themselves a hiring signal, and this is the one place we
    # can count them exactly rather than inferring from prose.
    if result.new > 0:
        _, is_new = await deduplicate_event(
            db,
            ExtractedEvent(
                event_type="career_page_update",
                title=f"{company.name} posted {result.new} new roles",
                event_occurred_at=datetime.now(UTC),
                structured_data={"new_postings": result.new},
                evidence_excerpt=f"{result.new} new postings on {source.url}",
                confidence=1.0,
            ),
            company.id,
            source.url,
        )
        events = int(is_new)

    await compute_score(db, company.id, commit=False)
    summary = (
        f"{result.new} new, {result.updated} updated, {result.closed} closed jobs"
    )
    return events, summary


async def _crawl_news(db: AsyncSession, source: Source) -> tuple[int, str]:
    """News APIs sweep a batch of companies rather than one fixed URL."""

    companies = list(
        (
            await db.execute(
                select(Company)
                .where(Company.is_active.is_(True))
                .order_by(Company.news_last_queried_at.asc().nullsfirst())
                .limit(NEWS_COMPANY_BATCH)
            )
        )
        .scalars()
        .all()
    )
    if not companies:
        return 0, "no companies to query"

    limiter_redis = await _redis_client()
    fetcher = NewsAPIFetcher(
        quota=QuotaTracker(limiter_redis),
        companies=[(c.name, c.aliases) for c in companies],
    )
    result = await fetcher.fetch(source.url)

    now = datetime.now(UTC)
    for company in companies:
        company.news_last_queried_at = now

    articles = [
        NewsArticle(**{**a, "published_at": None})
        for a in json.loads(result.content)
    ]
    total_events = 0
    matched = 0

    for article in articles:
        text = article.text
        if not is_relevant(text, source.source_type):
            continue
        # Which company an article is about is not knowable from the query
        # alone — a piece about Razorpay may mention five other companies — so
        # this only gates whether the article is worth an LLM call. Attribution
        # happens per event, inside _extract_and_store.
        if not await find_companies_in_text(db, text):
            logger.debug("no tracked company in article: %s", article.title[:80])
            continue
        matched += 1
        events, touched = await _extract_and_store(
            db, text, None, article.url, route_per_event=True
        )
        total_events += events
        for company_id in touched:
            await compute_score(db, company_id, commit=False)

    if limiter_redis is not None:
        await limiter_redis.aclose()

    return total_events, (
        f"{len(articles)} articles, {matched} matched a company, "
        f"{total_events} new events"
    )


async def _redis_client():
    if not settings.redis_url:
        return None
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, socket_connect_timeout=5,
                          decode_responses=True)
        await client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unavailable: %s", exc)
        return None


_FETCHERS = {
    "rss": RSSFetcher,
    "static_http": StaticFetcher,
    "playwright": PlaywrightFetcher,
}


async def _discover_board(
    db: AsyncSession, source: Source, result: FetchResult
) -> str | None:
    """Register an ATS board referenced by this page, if there is one.

    Most companies publish a careers page that is a wrapper around a hosted
    board, so the page itself has nothing to extract while a fully structured
    feed sits one iframe away. Finding it converts a fragile HTML scrape into
    the reliable ats_api path, with real per-role apply links.

    Returns a summary line when a board was newly registered, else None.
    """
    if source.company_id is None or source.fetch_tier == "ats_api":
        return None
    if not result.raw_html:
        return None

    board_url = discover_ats_board(result.raw_html)
    if board_url is None:
        return None

    try:
        created = await register_discovered_board(db, source.company_id, board_url)
    except SSRFError as exc:
        logger.warning("discovered board %s rejected: %s", board_url, exc)
        return None

    if created is None:
        return None
    return f"discovered ATS board {board_url}"


def _feed_entries(result: FetchResult, source: Source) -> list[dict]:
    """Split a fetched payload into the entries the extractor should see.

    The RSS fetcher emits a JSON list of {title, link, published_at, body}
    precisely so one post is extracted at a time. Static HTTP and Playwright
    emit a single document, which becomes a one-entry list carrying the
    source's own URL.
    """
    try:
        entries = json.loads(result.content)
    except json.JSONDecodeError:
        entries = None
    if not isinstance(entries, list):
        return [{"title": "", "body": result.content, "link": source.url}]
    return [entry for entry in entries if isinstance(entry, dict)]


async def _crawl_content(db: AsyncSession, source: Source) -> tuple[int, str, bool]:
    """RSS, static HTTP and Playwright all share this path."""
    # Validated again at crawl time, not just at submission: DNS can be
    # repointed at an internal address in between.
    validate_url(source.url)

    limiter = await build_rate_limiter(settings.redis_url)
    if not await limiter.acquire(source.url):
        source.next_crawl_at = datetime.now(UTC) + timedelta(seconds=60)
        _log(db, source, "rate_limited")
        return 0, "rate limited, rescheduled", False

    fetcher_cls = _FETCHERS[source.fetch_tier]
    result = await fetcher_cls().fetch(source.url)
    if not result.ok:
        raise RuntimeError(result.error or f"fetch failed with {result.http_status}")

    changed = result.content_hash != source.content_hash
    if not changed:
        _log(db, source, "skipped_unchanged", result=result, changed=False)
        return 0, "content unchanged", False

    source.content_hash = result.content_hash

    # Ahead of the pre-filter deliberately. A careers page whose visible text
    # is "there are no openings currently" gets rejected as irrelevant, yet it
    # still names the board it embeds — and that board is the whole prize.
    board = await _discover_board(db, source, result)

    if not is_relevant(result.content, source.source_type):
        _log(db, source, "success", result=result, text=result.content, changed=True)
        return 0, board or "pre-filter rejected", True

    if source.company_id is None:
        # A news site with no company attached: resolve per entry instead.
        return await _crawl_unattached(db, source, result)

    # A careers page yields job rows, not prose signals. Skipped once a board
    # has been discovered for this company — the ATS feed is strictly better
    # data, and paying an LLM to re-read the wrapper around it is waste.
    if source.source_type == "career_page" and result.raw_html:
        return await _crawl_career_page(db, source, result)

    # Per entry, not over the whole payload. A feed carries up to 25 posts and
    # only the entry knows its own article URL — attributing the batch to
    # source.url filed every event's evidence against the feed itself, so
    # "view source" opened raw XML instead of the post.
    events = 0
    for entry in _feed_entries(result, source):
        text = f"{entry.get('title', '')}\n{entry.get('body', '')}".strip()
        if not text:
            continue
        # The rule-based pre-filter again per entry: without it, splitting one
        # LLM call into 25 would multiply extraction cost by the feed length.
        if not is_relevant(text, source.source_type):
            continue
        created, _ = await _extract_and_store(
            db, text, source.company_id, entry.get("link") or source.url
        )
        events += created

    _log(
        db, source, "success", result=result, events=events,
        text=result.content, changed=True,
    )
    await compute_score(db, source.company_id, commit=False)
    return events, f"{events} new events", True


async def _crawl_career_page(
    db: AsyncSession, source: Source, result: FetchResult
) -> tuple[int, str, bool]:
    """Read open roles off a company's own careers page."""
    company = await db.get(Company, source.company_id)
    if company is None:
        raise ValueError("career page source has no company attached")

    # If this company already has a working ATS board, that feed supersedes
    # anything scraped here — same roles, better fields, no LLM cost.
    board = await db.scalar(
        select(Source).where(
            Source.company_id == company.id,
            Source.fetch_tier == "ats_api",
            Source.status == "approved",
        )
    )
    if board is not None:
        _log(db, source, "success", result=result, text=result.content, changed=True)
        return 0, f"skipped, ATS board already tracked ({board.url})", True

    linked = html_to_linked_text(result.raw_html or "", source.url)
    postings = await extract_career_page_jobs(linked, source.url)

    # None means the extractor failed, which is not the same as "no roles".
    # Reconciling on it would write nothing and — with the close pass off —
    # merely waste a crawl, but the distinction is worth keeping explicit.
    if postings is None:
        _log(db, source, "success", result=result, text=result.content, changed=True)
        return 0, "extractor unavailable, nothing written", True

    sync = await reconcile_jobs(
        db, company, source, postings, allow_close=False
    )

    events = 0
    if sync.new > 0:
        _, is_new = await deduplicate_event(
            db,
            ExtractedEvent(
                event_type="career_page_update",
                title=f"{company.name} listed {sync.new} new roles",
                event_occurred_at=datetime.now(UTC),
                structured_data={"new_postings": sync.new},
                evidence_excerpt=f"{sync.new} new roles on {source.url}",
                confidence=0.8,
            ),
            company.id,
            source.url,
        )
        events = int(is_new)

    _log(
        db, source, "success", result=result, events=events,
        text=result.content, changed=True,
    )
    await compute_score(db, company.id, commit=False)
    return events, f"{sync.new} new, {sync.updated} updated roles", True


async def _crawl_unattached(
    db: AsyncSession, source: Source, result: FetchResult
) -> tuple[int, str, bool]:
    """A news-site feed covering many companies."""

    events = 0
    matched = 0
    for entry in _feed_entries(result, source):
        text = f"{entry.get('title', '')}\n{entry.get('body', '')}".strip()
        if not is_relevant(text, source.source_type):
            continue
        if not await find_companies_in_text(db, text):
            continue
        matched += 1
        created, touched = await _extract_and_store(
            db, text, None, entry.get("link") or source.url, route_per_event=True
        )
        events += created
        for company_id in touched:
            await compute_score(db, company_id, commit=False)

    _log(
        db, source, "success", result=result, events=events,
        text=result.content, changed=True,
    )
    return events, f"{matched} entries matched a company, {events} new events", True


# --- tasks -------------------------------------------------------------------


async def _tick(db: AsyncSession) -> list[str]:
    now = datetime.now(UTC)
    due = list(
        (
            await db.execute(
                select(Source)
                .where(
                    Source.status == "approved",
                    Source.next_crawl_at.is_not(None),
                    Source.next_crawl_at <= now,
                )
                .order_by(Source.next_crawl_at)
                .limit(DISPATCH_BATCH)
                # Two overlapping ticks must not dispatch the same source; the
                # second simply skips whatever the first has locked.
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for source in due:
        source.next_crawl_at = now + timedelta(minutes=DISPATCH_LEASE_MINUTES)

    await db.commit()
    return [str(s.id) for s in due]


@celery_app.task(name="workers.crawl.tick_scheduler")
def tick_scheduler() -> dict[str, object]:
    """Dispatch whatever is due. Beat runs this every 5 minutes."""
    source_ids = run_async(with_session(_tick))
    for source_id in source_ids:
        crawl_source.delay(source_id)
    if source_ids:
        logger.info("dispatched %d crawl tasks", len(source_ids))
    return {"dispatched": len(source_ids)}


async def _crawl(db: AsyncSession, source_id: str) -> dict[str, object]:
    source = await db.get(Source, uuid.UUID(source_id))
    if source is None:
        return {"error": "source not found", "source_id": source_id}

    started = datetime.now(UTC)
    source.last_crawl_at = started
    source.total_crawls = (source.total_crawls or 0) + 1

    try:
        if source.fetch_tier == "ats_api":
            events, summary = await _crawl_ats(db, source)
            changed = True
        elif source.fetch_tier in NEWS_TIERS:
            events, summary = await _crawl_news(db, source)
            changed = True
            _log(db, source, "success", events=events)
        else:
            events, summary, changed = await _crawl_content(db, source)

        if source.fetch_tier == "ats_api":
            _log(db, source, "success", events=events, text=summary)

        source.last_successful_crawl_at = started
        source.total_events_extracted = (source.total_events_extracted or 0) + events
        _schedule_success(source, changed=changed)
        await db.commit()

        logger.info("crawled %s: %s", source.url, summary)
        return {"source": source.url, "events": events, "summary": summary}

    except HostResolutionError as exc:
        # DNS said nothing. That is a dead host or a resolver blip, not an
        # attempt to reach somewhere internal — so it backs off like any other
        # failure and disables only after repeated attempts.
        await db.rollback()
        source = await db.get(Source, uuid.UUID(source_id))
        if source is not None:
            _schedule_failure(source, f"DNS: {exc}")
            _log(db, source, "failure", error=str(exc))
            await db.commit()
        logger.warning("could not resolve %s: %s", source_id, exc)
        return {"error": "host_unresolvable", "detail": str(exc)}

    except SSRFError as exc:
        # Resolved, but to an address we refuse to fetch. That is not transient;
        # stop crawling it rather than retrying into a backoff.
        await db.rollback()
        source = await db.get(Source, uuid.UUID(source_id))
        if source is not None:
            source.status = "disabled"
            source.last_failure_reason = f"SSRF: {exc}"
            _log(db, source, "failure", error=str(exc))
            await db.commit()
        logger.warning("disabled %s after SSRF failure: %s", source_id, exc)
        return {"error": "ssrf_blocked", "detail": str(exc)}

    except Exception as exc:  # noqa: BLE001 — one bad source must not kill the worker
        await db.rollback()
        source = await db.get(Source, uuid.UUID(source_id))
        if source is not None:
            _schedule_failure(source, str(exc))
            _log(db, source, "failure", error=str(exc))
            await db.commit()
        logger.warning("crawl failed for %s: %s", source_id, exc)
        return {"error": str(exc)}


@celery_app.task(bind=True, name="workers.crawl.crawl_source", max_retries=1)
def crawl_source(self, source_id: str) -> dict[str, object]:
    """Run the full pipeline for one source.

    Never re-raises: failures are recorded on the source and retried through
    `next_crawl_at` backoff, which is durable, rather than through Celery
    retries, which are not.
    """
    return run_async(with_session(lambda db: _crawl(db, source_id)))
