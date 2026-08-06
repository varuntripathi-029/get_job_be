"""CompanyScore model — append-only history of computed momentum scores.

Rows are never updated. Recomputing writes a new row so a score shown to a user
months ago can still be reproduced, along with the events that produced it.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin

MOMENTUM_LABELS = ("none", "low", "moderate", "high", "very_high")


class CompanyScore(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "company_scores"
    __table_args__ = (
        CheckConstraint(
            "momentum_label IN ('none', 'low', 'moderate', 'high', 'very_high')",
            name="ck_company_scores_momentum_label",
        ),
        CheckConstraint(
            "momentum_score >= 0 AND momentum_score <= 100",
            name="ck_company_scores_momentum_range",
        ),
        Index("ix_company_scores_company_scored", "company_id", "scored_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    score_version: Mapped[str] = mapped_column(Text, nullable=False)
    momentum_score: Mapped[float] = mapped_column(Float, nullable=False)
    momentum_label: Mapped[str] = mapped_column(Text, nullable=False)
    signal_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    contributing_event_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), nullable=True
    )
    score_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CompanyScore {self.momentum_label} {self.momentum_score:.1f}>"
