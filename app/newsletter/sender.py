"""Email delivery via Resend, with free-tier rate limiting."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.newsletter import tokens
from app.newsletter.models import NewsletterSubscriber
from app.newsletter.schemas import NewsletterContent, SendResult
from app.newsletter.templates import render_newsletter_html

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT = 20.0

# Redis holds the daily counter. Losing it means at worst a day's cap resets —
# acceptable, unlike losing an unsubscribe token, which is why those are signed
# rather than stored.
_DAILY_KEY_TTL_SECONDS = 48 * 3600


async def send_email(to: str, subject: str, html: str) -> bool:
    """Send one email. Returns False on failure rather than raising.

    The `resend` package is synchronous and would block the event loop, so the
    REST call goes through httpx directly.
    """
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY is unset — not sending to %s", to)
        return False

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
    except httpx.HTTPError as exc:
        logger.error("Resend request failed for %s: %s", to, exc)
        return False

    if response.status_code >= 400:
        # Logged at error level with the body — Resend's failures are almost
        # always actionable config problems (unverified domain, restricted key).
        logger.error(
            "Resend rejected send to %s (%d): %s",
            to,
            response.status_code,
            response.text[:300],
        )
        return False

    logger.debug("sent email to %s", to)
    return True


def _daily_key() -> str:
    return f"resend_daily:{datetime.now(UTC).date().isoformat()}"


async def _redis():
    """Redis client, or None if unreachable.

    Rate limiting degrades to "no cap" rather than blocking all sends, since a
    Redis outage should not silently stop the newsletter.
    """
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 — any connection failure is the same here
        logger.warning("Redis unavailable, sending without a daily cap: %s", exc)
        return None


async def send_newsletter(
    db, content: NewsletterContent, *, subscribers: list[NewsletterSubscriber]
) -> SendResult:
    """Send the digest to every confirmed subscriber, one email each.

    Rendering happens per recipient because the unsubscribe link is
    subscriber-specific — a shared link would unsubscribe the wrong person.
    """
    result = SendResult(
        edition_number=content.edition_number, recipients=len(subscribers)
    )
    if not subscribers:
        logger.info("no confirmed subscribers; nothing to send")
        return result

    redis = await _redis()
    sent_today = 0
    if redis is not None:
        sent_today = int(await redis.get(_daily_key()) or 0)

    base_url = settings.frontend_url

    for index, subscriber in enumerate(subscribers):
        if redis is not None and sent_today >= settings.newsletter_daily_send_limit:
            remaining = len(subscribers) - index
            result.skipped_rate_limit = remaining
            logger.warning(
                "hit the %d/day Resend cap — %d subscribers deferred to tomorrow",
                settings.newsletter_daily_send_limit,
                remaining,
            )
            break

        html = render_newsletter_html(
            content, tokens.unsubscribe_url(subscriber.id), base_url
        )
        if await send_email(subscriber.email, content.subject, html):
            result.sent += 1
            sent_today += 1
            if redis is not None:
                await redis.incr(_daily_key())
                await redis.expire(_daily_key(), _DAILY_KEY_TTL_SECONDS)
        else:
            result.failed += 1

        # Resend rate-limits to 2 requests/second; one per second stays clear.
        if index < len(subscribers) - 1:
            await asyncio.sleep(settings.newsletter_send_delay_seconds)

    if redis is not None:
        await redis.aclose()

    logger.info(
        "newsletter edition #%d sent to %d subscribers, %d failures, %d deferred",
        result.edition_number,
        result.sent,
        result.failed,
        result.skipped_rate_limit,
    )
    return result


async def next_edition_number() -> int:
    """Monotonic edition counter. Falls back to 1 when Redis is unavailable."""
    redis = await _redis()
    if redis is None:
        return 1
    try:
        return int(await redis.incr("newsletter:edition"))
    finally:
        await redis.aclose()


async def current_edition_number() -> int:
    """Read the counter without advancing it — used by the admin preview."""
    redis = await _redis()
    if redis is None:
        return 1
    try:
        return int(await redis.get("newsletter:edition") or 0) + 1
    finally:
        await redis.aclose()
