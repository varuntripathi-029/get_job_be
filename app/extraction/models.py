"""Event model — a structured hiring signal extracted from source content.

Bitemporal on purpose: `event_occurred_at` is when the thing happened in the world,
`observed_at` is when we first saw it. Scoring decays on the former and falls back
to the latter when a source gives no date.

Dedup lineage lives on the row itself: duplicates point at the canonical event via
`canonical_event_id` and flip `is_canonical` false, so evidence accumulates on one row.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

EVENT_TYPES = (
    "funding",
    "new_office",
    "leadership_change",
    "product_launch",
    "engineering_expansion",
    "ai_division",
    "infrastructure_investment",
    "acquisition",
    "partnership",
    "layoff",
    "career_page_update",
)

EVENT_STATUSES = ("active", "merged", "retracted")


class Event(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'funding', 'new_office', 'leadership_change', 'product_launch', "
            "'engineering_expansion', 'ai_division', 'infrastructure_investment', "
            "'acquisition', 'partnership', 'layoff', 'career_page_update')",
            name="ck_events_event_type",
        ),
        CheckConstraint(
            "status IN ('active', 'merged', 'retracted')",
            name="ck_events_status",
        ),
        # Scoring reads only canonical active events for one company.
        Index(
            "ix_events_company_occurred",
            "company_id",
            "event_occurred_at",
            postgresql_where=text("is_canonical AND status = 'active'"),
        ),
        Index("ix_events_type_occurred", "event_type", "event_occurred_at"),
        Index("ix_events_observed_at", "observed_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)

    event_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Array of {source_url, excerpt, reliability_tier, published_at}.
    evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    canonical_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    is_canonical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")

    def __repr__(self) -> str:
        return f"<Event {self.event_type} company={self.company_id}>"
