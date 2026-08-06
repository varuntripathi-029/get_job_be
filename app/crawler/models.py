"""CrawlLog model — one row per fetch attempt.

High churn and purely diagnostic, so rows are deleted after
`settings.crawl_log_retention_days` by the cleanup worker.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin

CRAWL_STATUSES = ("success", "failure", "skipped_unchanged", "rate_limited")


class CrawlLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "crawl_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failure', 'skipped_unchanged', 'rate_limited')",
            name="ck_crawl_logs_status",
        ),
        Index("ix_crawl_logs_source_created", "source_id", "created_at"),
        Index("ix_crawl_logs_created_at", "created_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_changed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Readable text with HTML stripped. NULL when the fetch was skipped as unchanged.
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    events_extracted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CrawlLog {self.status} source={self.source_id}>"
