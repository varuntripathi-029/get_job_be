"""Weekly newsletter generation and delivery."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.newsletter import sender
from app.newsletter import service as newsletter_service
from app.newsletter.generator import generate_newsletter
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run(db: AsyncSession) -> dict[str, object]:
    subscribers = await newsletter_service.list_active_subscribers(db)
    if not subscribers:
        logger.info("no confirmed subscribers — skipping this week's newsletter")
        return {"sent": 0, "skipped": "no_subscribers"}

    # The edition number is only consumed once there is something to send, so a
    # quiet week does not leave a gap in the sequence.
    content = await generate_newsletter(db, edition_number=1)
    if content.is_empty:
        logger.info("No newsletter content this week")
        return {"sent": 0, "skipped": "no_content"}

    content.edition_number = await sender.next_edition_number()
    result = await sender.send_newsletter(db, content, subscribers=subscribers)
    return {
        "edition": result.edition_number,
        "sent": result.sent,
        "failed": result.failed,
        "deferred": result.skipped_rate_limit,
        "recipients": result.recipients,
    }


@celery_app.task(name="workers.newsletter.generate_and_send_newsletter")
def generate_and_send_newsletter() -> dict[str, object]:
    """Assemble the weekly digest and mail it to confirmed subscribers."""
    return run_async(with_session(_run))
