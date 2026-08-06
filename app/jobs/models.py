"""Job model — an individual requisition synced from an ATS.

Jobs are a timeline, not a snapshot: rows are never deleted when a posting
disappears from the ATS. `first_seen_at` / `closed_at` bracket its life so we can
tell "posted 40 roles last month" from "has 40 roles open".
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

ROLE_FAMILIES = (
    "engineering",
    "product",
    "design",
    "data",
    "devops",
    "marketing",
    "sales",
    "operations",
    "hr",
    "finance",
    "legal",
    "other",
)

SENIORITIES = (
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "director",
    "vp",
    "c_level",
)

EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship")
WORK_MODES = ("remote", "hybrid", "onsite")

EMBEDDING_DIM = 768


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "role_family IS NULL OR role_family IN ("
            "'engineering', 'product', 'design', 'data', 'devops', 'marketing', "
            "'sales', 'operations', 'hr', 'finance', 'legal', 'other')",
            name="ck_jobs_role_family",
        ),
        CheckConstraint(
            "seniority IS NULL OR seniority IN ("
            "'intern', 'junior', 'mid', 'senior', 'staff', 'principal', "
            "'director', 'vp', 'c_level')",
            name="ck_jobs_seniority",
        ),
        CheckConstraint(
            "employment_type IS NULL OR employment_type IN ("
            "'full_time', 'part_time', 'contract', 'internship')",
            name="ck_jobs_employment_type",
        ),
        CheckConstraint(
            "work_mode IS NULL OR work_mode IN ('remote', 'hybrid', 'onsite')",
            name="ck_jobs_work_mode",
        ),
        # ATS ids are unique per company; the diff in sync.py relies on this.
        Index(
            "uq_jobs_company_external",
            "company_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_jobs_active_role_family", "is_active", "role_family"),
        Index("ix_jobs_skills_gin", "skills", postgresql_using="gin"),
        Index(
            "ix_jobs_fts",
            text(
                "to_tsvector('english', "
                "title || ' ' || coalesce(description_text, ''))"
            ),
            postgresql_using="gin",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_family: Mapped[str | None] = mapped_column(Text, nullable=True)
    seniority: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_mode: Mapped[str | None] = mapped_column(Text, nullable=True)

    location_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # Populated later, once embeddings are wired up. No HNSW index yet.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Job {self.title!r} company={self.company_id}>"
