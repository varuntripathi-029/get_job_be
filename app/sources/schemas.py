"""Source request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.sources.models import FETCH_TIERS, SOURCE_TYPES, STATUSES


class SourceSubmit(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    source_type: str = Field(pattern="|".join(SOURCE_TYPES))
    company_id: uuid.UUID | None = None
    # Overrides auto-detection when the submitter knows better.
    fetch_tier: str | None = Field(default=None, pattern="|".join(FETCH_TIERS))
    notes: str | None = Field(default=None, max_length=500)


class SourceAdminCreate(SourceSubmit):
    """Admin-created sources skip the pending queue."""

    crawl_frequency_minutes: int | None = Field(default=None, ge=5, le=100_800)
    reliability_score: float | None = Field(default=None, ge=0.0, le=1.0)


class SourceUpdate(BaseModel):
    crawl_frequency_minutes: int | None = Field(default=None, ge=5, le=100_800)
    status: str | None = Field(default=None, pattern="|".join(STATUSES))
    fetch_tier: str | None = Field(default=None, pattern="|".join(FETCH_TIERS))
    requires_js: bool | None = None
    reliability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    next_crawl_at: datetime | None = None


class SourceReject(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID | None
    url: str
    source_type: str
    fetch_tier: str
    status: str
    rejection_reason: str | None
    crawl_frequency_minutes: int
    next_crawl_at: datetime | None
    last_crawl_at: datetime | None
    last_successful_crawl_at: datetime | None
    consecutive_failures: int
    requires_js: bool
    reliability_score: float | None
    total_crawls: int
    total_events_extracted: int
    created_at: datetime


class CrawlerHealthRow(BaseModel):
    """One row of the admin crawler-health table."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    url: str
    company_name: str | None
    source_type: str
    fetch_tier: str
    status: str
    last_crawl_at: datetime | None
    last_successful_crawl_at: datetime | None
    next_crawl_at: datetime | None
    consecutive_failures: int
    last_failure_reason: str | None
    content_hash: str | None
    total_crawls: int
    total_events_extracted: int


class SourceBrowseItem(BaseModel):
    """One tracked source, as shown on the public coverage page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    source_type: str
    fetch_tier: str
    last_crawl_at: datetime | None = None


class CompanySourceGroup(BaseModel):
    slug: str
    name: str
    sources: list[SourceBrowseItem] = Field(default_factory=list)


class SourceBrowseResponse(BaseModel):
    companies: list[CompanySourceGroup] = Field(default_factory=list)
    # Sources not tied to a company (news sites, news APIs) crawl across all of
    # them, so they are listed separately rather than hidden.
    global_sources: list[SourceBrowseItem] = Field(default_factory=list)


class SourceStats(BaseModel):
    total_sources: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    companies_with_sources: int
    companies_without_sources: int
