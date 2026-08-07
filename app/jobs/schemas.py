"""Job request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str | None = None
    company_slug: str | None = None

    title: str
    department: str | None
    role_family: str | None
    seniority: str | None
    employment_type: str | None
    work_mode: str | None
    location_raw: str | None
    location_normalized: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    skills: list[str] | None
    application_url: str | None

    first_seen_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    is_active: bool


class JobDetailResponse(JobResponse):
    description_text: str | None
