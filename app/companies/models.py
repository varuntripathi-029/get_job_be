"""Company model — the entity hiring momentum is computed for."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

STAGES = (
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c",
    "growth",
    "public",
    "bootstrapped",
    "unknown",
)


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "stage IS NULL OR stage IN ("
            "'pre_seed', 'seed', 'series_a', 'series_b', 'series_c', "
            "'growth', 'public', 'bootstrapped', 'unknown')",
            name="ck_companies_stage",
        ),
        Index("ix_companies_aliases_gin", "aliases", postgresql_using="gin"),
    )

    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_domain: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True
    )
    # Name variants used for entity resolution against news articles.
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    headcount_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_hq: Mapped[str | None] = mapped_column(Text, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ats_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_board_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Round-robin cursor for the news APIs: they query a batch of companies per
    # tick, oldest-queried first. Kept in Postgres rather than Redis so cycling
    # 500 companies costs no Upstash commands.
    news_last_queried_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Company {self.slug}>"
