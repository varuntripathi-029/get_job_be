"""Extraction request/response schemas."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.extraction.models import EVENT_TYPES
from app.jobs.models import ROLE_FAMILIES, SENIORITIES, WORK_MODES

MAX_SKILLS = 60
MAX_LOCATIONS = 10


def _clean_str_list(value: Any) -> Any:
    """Drop blanks, trim, de-duplicate case-insensitively, keep order.

    LLMs return ["Python", "python", " Docker "] often enough that normalising
    here is cheaper than doing it at every read site.
    """
    if not isinstance(value, list):
        return value
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
    return out


StrList = Annotated[list[str], BeforeValidator(_clean_str_list)]


def _coerce_optional(allowed: tuple[str, ...]):
    """Map an out-of-vocabulary or 'null'-ish value to None instead of failing.

    A resume is still useful with an unrecognised seniority; rejecting the whole
    parse over one bad enum would lose the skills too.
    """

    def validator(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip().lower()
        if text in ("", "null", "none", "unknown", "n/a"):
            return None
        return text if text in allowed else None

    return BeforeValidator(validator)


class ParsedResume(BaseModel):
    """Structured resume fields as returned by the parsing prompt."""

    # Caps are applied by truncation in model_post_init, not by max_length:
    # a validation error would discard the entire parse over a chatty model
    # listing 200 "skills", when the first 60 are perfectly usable.
    skills: StrList = Field(default_factory=list)
    role_families: StrList = Field(default_factory=list)
    seniority: Annotated[str | None, _coerce_optional(SENIORITIES)] = None
    experience_years: float | None = Field(default=None, ge=0, le=60)
    locations: StrList = Field(default_factory=list)
    work_mode_preference: Annotated[str | None, _coerce_optional(WORK_MODES)] = None

    def model_post_init(self, _context: Any) -> None:
        # Silently drop role families outside the vocabulary — the CHECK
        # constraint on jobs.role_family uses the same list, so an unknown value
        # would never match a job anyway.
        allowed = set(ROLE_FAMILIES)
        self.role_families = [r for r in self.role_families if r.lower() in allowed]
        self.skills = self.skills[:MAX_SKILLS]
        self.locations = self.locations[:MAX_LOCATIONS]


class EventResponse(BaseModel):
    """A public-facing hiring signal with its evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str | None = None
    company_slug: str | None = None
    event_type: str
    title: str
    event_occurred_at: datetime | None
    observed_at: datetime
    structured_data: dict | None
    evidence: list
    source_count: int
    extraction_confidence: float | None


EVENT_TYPE_VALUES = EVENT_TYPES


class ClassificationResult(BaseModel):
    """Output of the cheap relevance gate."""

    is_relevant: bool = True
    reason: str = ""
    model: str | None = None
    latency_ms: int | None = None


class ExtractedEvent(BaseModel):
    """One event as returned by the extractor, before dedup and persistence."""

    event_type: str
    title: str = Field(min_length=1, max_length=500)
    event_occurred_at: datetime | None = None
    structured_data: dict = Field(default_factory=dict)
    evidence_excerpt: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("event_type")
    @classmethod
    def _known_event_type(cls, v: str) -> str:
        normalized = (v or "").strip().lower()
        if normalized not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {v!r}")
        return normalized

    @field_validator("event_occurred_at", mode="before")
    @classmethod
    def _parse_date(cls, v: Any) -> Any:
        """Accept the YYYY-MM-DD the prompt asks for, and tolerate the rest.

        Models return 'null', '', 'unknown' and full timestamps interchangeably;
        an unparseable date should cost the date, not the event.
        """
        if v is None or isinstance(v, datetime):
            return v
        if not isinstance(v, str):
            return None
        text = v.strip()
        if text.lower() in ("", "null", "none", "unknown", "n/a"):
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        # Naive dates are treated as UTC; the column is timestamptz and a naive
        # value would otherwise be rejected by asyncpg.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @field_validator("evidence_excerpt")
    @classmethod
    def _cap_excerpt(cls, v: str) -> str:
        return (v or "").strip()[:200]
