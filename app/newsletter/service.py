"""Newsletter subscription lifecycle."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError, ValidationError
from app.common.pagination import PaginationParams
from app.newsletter import sender, tokens
from app.newsletter.models import NewsletterSubscriber
from app.newsletter.templates import (
    CONFIRMATION_SUBJECT,
    UNSUBSCRIBE_SUBJECT,
    render_confirmation_email,
    render_unsubscribe_email,
)

logger = logging.getLogger(__name__)

# Shown for both a new signup and a repeat one. Confirming or denying that an
# address is already subscribed would turn this public endpoint into a way to
# test whether someone uses the product.
SUBSCRIBE_MESSAGE = "Check your email for a confirmation link."


async def subscribe(
    db: AsyncSession, email: str, user_id: uuid.UUID | None = None
) -> NewsletterSubscriber:
    """Create or revive a subscription and send the confirmation email."""
    normalized = email.strip().lower()
    subscriber = await db.scalar(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == normalized)
    )

    if subscriber is None:
        subscriber = NewsletterSubscriber(
            email=normalized, user_id=user_id, is_active=False
        )
        db.add(subscriber)
    else:
        # Re-subscribing after unsubscribing starts the double opt-in over; the
        # row is never flipped active without a fresh confirmation click.
        subscriber.is_active = False
        subscriber.unsubscribed_at = None
        if user_id is not None:
            subscriber.user_id = user_id

    await db.commit()
    await db.refresh(subscriber)

    if subscriber.confirmed_at is None or not subscriber.is_active:
        await sender.send_email(
            subscriber.email,
            CONFIRMATION_SUBJECT,
            render_confirmation_email(tokens.confirm_url(subscriber.id)),
        )

    logger.info("subscription requested for %s", normalized)
    return subscriber


async def confirm_subscription(db: AsyncSession, token: str) -> NewsletterSubscriber:
    try:
        subscriber_id = tokens.verify(token, "confirm")
    except tokens.InvalidToken as exc:
        raise ValidationError(str(exc)) from exc

    subscriber = await _require(db, subscriber_id)

    # Idempotent: clicking a confirmation link twice is normal behaviour.
    if not subscriber.is_active:
        subscriber.is_active = True
        subscriber.confirmed_at = datetime.now(UTC)
        subscriber.unsubscribed_at = None
        await db.commit()
        await db.refresh(subscriber)
        logger.info("confirmed subscription for %s", subscriber.email)

    return subscriber


async def unsubscribe(db: AsyncSession, token: str) -> NewsletterSubscriber:
    try:
        subscriber_id = tokens.verify(token, "unsubscribe")
    except tokens.InvalidToken as exc:
        raise ValidationError(str(exc)) from exc

    subscriber = await _require(db, subscriber_id)

    if subscriber.is_active:
        subscriber.is_active = False
        subscriber.unsubscribed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(subscriber)
        logger.info("unsubscribed %s", subscriber.email)
        await sender.send_email(
            subscriber.email, UNSUBSCRIBE_SUBJECT, render_unsubscribe_email()
        )

    return subscriber


async def list_active_subscribers(db: AsyncSession) -> list[NewsletterSubscriber]:
    """Confirmed and still subscribed — the newsletter recipient list."""
    result = await db.execute(
        select(NewsletterSubscriber)
        .where(
            NewsletterSubscriber.is_active.is_(True),
            NewsletterSubscriber.confirmed_at.is_not(None),
        )
        .order_by(NewsletterSubscriber.created_at)
    )
    return list(result.scalars().all())


async def list_subscribers(
    db: AsyncSession, params: PaginationParams, *, is_active: bool | None = None
) -> tuple[list[NewsletterSubscriber], int]:
    stmt = select(NewsletterSubscriber)
    if is_active is not None:
        stmt = stmt.where(NewsletterSubscriber.is_active.is_(is_active))

    total = int(
        await db.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
        or 0
    )
    result = await db.execute(
        stmt.order_by(NewsletterSubscriber.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    return list(result.scalars().all()), total


async def _require(db: AsyncSession, subscriber_id: uuid.UUID) -> NewsletterSubscriber:
    subscriber = await db.get(NewsletterSubscriber, subscriber_id)
    if subscriber is None:
        raise NotFoundError("This subscription no longer exists.")
    return subscriber
