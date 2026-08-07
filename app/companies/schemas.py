"""Company request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.companies.models import STAGES


class CompanyBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canonical_domain: str = Field(min_length=3, max_length=253)
    aliases: list[str] | None = None
    description: str | None = None
    logo_url: str | None = None
    website: str | None = None
    industry: str | None = None
    stage: str | None = Field(default=None, pattern="|".join(STAGES))
    headcount_estimate: int | None = Field(default=None, ge=0)
    location_hq: str | None = None
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    ats_provider: str | None = None
    ats_board_url: str | None = None


class CompanyCreate(CompanyBase):
    slug: str | None = Field(
        default=None,
        description="Generated from name when omitted.",
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class CompanyUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    description: str | None = None
    logo_url: str | None = None
    website: str | None = None
    industry: str | None = None
    stage: str | None = Field(default=None, pattern="|".join(STAGES))
    headcount_estimate: int | None = Field(default=None, ge=0)
    location_hq: str | None = None
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    ats_provider: str | None = None
    ats_board_url: str | None = None
    is_active: bool | None = None


class CompanyResponse(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CompanyWithScore(CompanyResponse):
    """Company plus its most recent momentum score, when one exists."""

    momentum_score: float | None = None
    momentum_label: str | None = None
    scored_at: datetime | None = None


class ScorePoint(BaseModel):
    """One point on the momentum sparkline."""

    model_config = ConfigDict(from_attributes=True)

    momentum_score: float
    momentum_label: str
    score_delta: float | None = None
    scored_at: datetime


class CompanyListItem(CompanyResponse):
    """Row shape for the company list — score and job count folded in so the
    frontend does not need a request per card."""

    momentum_score: float | None = None
    momentum_label: str | None = None
    active_job_count: int = 0


class CompanySourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    source_type: str
    last_crawl_at: datetime | None = None


class CompanyDetailResponse(CompanyWithScore):
    """Everything the company page renders, in one round trip."""

    score_delta: float | None = None
    active_job_count: int = 0
    total_event_count: int = 0
    recent_events: list["EventResponse"] = Field(default_factory=list)
    # Oldest to newest, so a chart can plot it without reversing.
    score_history: list[ScorePoint] = Field(default_factory=list)
    sources: list[CompanySourceSummary] = Field(default_factory=list)


class CompanyComparison(BaseModel):
    """One company's slice of a side-by-side comparison."""

    slug: str
    name: str
    industry: str | None = None
    stage: str | None = None
    momentum_score: float | None = None
    momentum_label: str | None = None
    active_jobs: int = 0
    active_jobs_by_family: dict[str, int] = Field(default_factory=dict)
    recent_events: int = 0
    score_history: list[ScorePoint] = Field(default_factory=list)
    top_events: list["EventResponse"] = Field(default_factory=list)


class CompareResponse(BaseModel):
    companies: list[CompanyComparison] = Field(default_factory=list)


# Imported at the bottom and rebuilt to avoid a circular import: the extraction
# schemas import nothing from companies, but the reverse edge is needed here.
from app.extraction.schemas import EventResponse  # noqa: E402

CompanyDetailResponse.model_rebuild()
CompanyComparison.model_rebuild()
