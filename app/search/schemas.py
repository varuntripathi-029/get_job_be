"""Search response schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SearchType = Literal["all", "company", "job", "event"]

MIN_QUERY_LENGTH = 2
# With type=all each section is a preview, not a page — the frontend shows
# "3 companies, 12 jobs" and links to the filtered view for the rest.
PREVIEW_SIZE = 5


class CompanySearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    industry: str | None = None
    stage: str | None = None
    momentum_score: float | None = None
    momentum_label: str | None = None


class JobSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    company_name: str | None = None
    company_slug: str | None = None
    role_family: str | None = None
    seniority: str | None = None
    work_mode: str | None = None
    location_raw: str | None = None
    is_active: bool
    first_seen_at: datetime


class EventSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    title: str
    company_name: str | None = None
    company_slug: str | None = None
    event_occurred_at: datetime | None = None
    observed_at: datetime
    extraction_confidence: float | None = None


class SearchSection[T](BaseModel):
    items: list[T]
    total: int


class SearchResponse(BaseModel):
    query: str
    type: SearchType
    companies: SearchSection[CompanySearchResult]
    jobs: SearchSection[JobSearchResult]
    events: SearchSection[EventSearchResult]
    total: int
    page: int
    per_page: int
