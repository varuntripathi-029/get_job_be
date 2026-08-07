"""Newsletter request/response schemas."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class SubscribeRequest(BaseModel):
    email: EmailStr


class SubscribeResponse(BaseModel):
    message: str


class TokenRequest(BaseModel):
    token: str


class SubscriberAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    user_id: uuid.UUID | None
    is_active: bool
    confirmed_at: datetime | None
    unsubscribed_at: datetime | None
    created_at: datetime


# --- Newsletter content ------------------------------------------------------
# Dataclasses rather than Pydantic models: this is internal render input, built
# from SQL rows and consumed by a template, never parsed from untrusted JSON.


@dataclass(slots=True)
class MoverEntry:
    name: str
    slug: str
    momentum_score: float
    momentum_label: str
    delta: float


@dataclass(slots=True)
class HotspotEntry:
    name: str
    slug: str
    new_jobs: int


@dataclass(slots=True)
class EventEntry:
    company_name: str
    company_slug: str
    event_type: str
    title: str
    occurred_at: datetime | None
    evidence_url: str | None


@dataclass(slots=True)
class CompanyEntry:
    name: str
    slug: str
    industry: str | None


@dataclass(slots=True)
class NewsletterContent:
    subject: str
    top_movers: list[MoverEntry] = field(default_factory=list)
    hiring_hotspots: list[HotspotEntry] = field(default_factory=list)
    notable_events: list[EventEntry] = field(default_factory=list)
    new_entrants: list[CompanyEntry] = field(default_factory=list)
    generated_at: datetime | None = None
    edition_number: int = 1

    @property
    def is_empty(self) -> bool:
        """Nothing worth mailing. Sending an empty digest trains people to
        ignore the next one."""
        return not (self.top_movers or self.hiring_hotspots or self.notable_events)


@dataclass(slots=True)
class SendResult:
    edition_number: int
    sent: int = 0
    failed: int = 0
    skipped_rate_limit: int = 0
    recipients: int = 0
