"""Dashboard response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class QuotaRow(BaseModel):
    api: str
    used: int
    limit: int
    remaining: int
    exhausted: bool


class CrawlBudgetResponse(BaseModel):
    """Whether third-party lookups are still affordable today.

    Public on purpose. When the free-tier budget runs out the site stops
    discovering new roles, and saying so plainly is better than letting the
    feed look stale for no visible reason.
    """

    paused: bool
    resets_at: datetime
    message: str | None = None
    vendors: list[QuotaRow]


class DashboardStats(BaseModel):
    total_companies: int
    total_active_jobs: int
    total_events_30d: int
    total_sources: int
    last_crawl_at: datetime | None = None


class TrendingCompany(BaseModel):
    slug: str
    name: str
    industry: str | None = None
    momentum_score: float
    momentum_label: str
    score_delta: float
    top_signal: str | None = None
    active_jobs: int = 0


class TrendingResponse(BaseModel):
    trending: list[TrendingCompany]


class ActivityEvent(BaseModel):
    id: uuid.UUID
    event_type: str
    title: str
    company_name: str
    company_slug: str
    observed_at: datetime
    event_occurred_at: datetime | None = None
    source_count: int


class ActivityResponse(BaseModel):
    events: list[ActivityEvent]


class IndustryBreakdown(BaseModel):
    name: str
    count: int
    avg_score: float | None = None


class IndustriesResponse(BaseModel):
    industries: list[IndustryBreakdown]
