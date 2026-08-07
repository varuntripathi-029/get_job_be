"""Resume model — parsed candidate profile, one per user.

Holds PII, so rows carry `expires_at` and are deleted by the cleanup worker
rather than kept indefinitely.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIM = 768

# Upload returns as soon as the text is extracted; parsing and embedding happen
# in a worker. This is how a client knows whether matching is ready yet.
INDEXING_STATUSES = ("pending", "processing", "ready", "failed")


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint(
            "work_mode_preference IS NULL OR work_mode_preference IN "
            "('remote', 'hybrid', 'onsite')",
            name="ck_resumes_work_mode_preference",
        ),
        CheckConstraint(
            "indexing_status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_resumes_indexing_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    parsed_skills: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    parsed_role_families: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    parsed_seniority: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    parsed_locations: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    work_mode_preference: Mapped[str | None] = mapped_column(Text, nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    indexing_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending"
    )
    indexing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # PII lifecycle — cleanup worker deletes rows past this.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Resume user={self.user_id}>"
