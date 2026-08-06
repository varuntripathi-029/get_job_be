"""Source model — the registry of things the crawler polls.

`next_crawl_at` is what makes the database the scheduler: the Celery beat tick
selects due rows rather than holding an in-memory schedule.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

SOURCE_TYPES = (
    "career_page",
    "engineering_blog",
    "company_blog",
    "news_site",
    "rss_feed",
    "ats_api",
    "github_org",
    "news_api",
    "search_api",
)

FETCH_TIERS = (
    "ats_api",
    "rss",
    "static_http",
    "playwright",
    "news_api",
    "search_api",
)

STATUSES = ("pending", "approved", "rejected", "disabled")


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ("
            "'career_page', 'engineering_blog', 'company_blog', 'news_site', "
            "'rss_feed', 'ats_api', 'github_org', 'news_api', 'search_api')",
            name="ck_sources_source_type",
        ),
        CheckConstraint(
            "fetch_tier IN ("
            "'ats_api', 'rss', 'static_http', 'playwright', 'news_api', 'search_api')",
            name="ck_sources_fetch_tier",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'disabled')",
            name="ck_sources_status",
        ),
        # The scheduler query. Partial index keeps it small as rejected rows pile up.
        Index(
            "ix_sources_due",
            "status",
            "next_crawl_at",
            postgresql_where=text("status = 'approved'"),
        ),
        Index("ix_sources_company_id", "company_id"),
    )

    # NULL for news/search APIs and news sites, which span many companies.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_tier: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawl_frequency_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1440"
    )
    next_crawl_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_crawl_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_crawl_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # SHA-256 of the last fetched body; lets us skip unchanged pages before parsing.
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_js: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    reliability_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default="0.7"
    )
    total_crawls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_events_extracted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    def __repr__(self) -> str:
        return f"<Source {self.source_type} {self.url}>"
