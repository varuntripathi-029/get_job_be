"""Resume request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.jobs.schemas import JobResponse


class ResumeResponse(BaseModel):
    """A parsed resume. Deliberately excludes `raw_text` — it is PII and no
    client needs it back."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str | None
    parsed_skills: list[str] | None
    parsed_role_families: list[str] | None
    parsed_seniority: str | None
    parsed_experience_years: float | None
    parsed_locations: list[str] | None
    work_mode_preference: str | None
    parsed_at: datetime | None
    expires_at: datetime | None
    has_embedding: bool = False


class JobMatchResponse(BaseModel):
    job: JobResponse
    similarity_score: float
    match_reasons: list[str]


class ResumeUploadResponse(BaseModel):
    resume: ResumeResponse
    matched_job_count: int
    # Surfaced so the UI can explain an empty match list instead of looking broken.
    warnings: list[str] = []
