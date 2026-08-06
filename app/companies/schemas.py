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
