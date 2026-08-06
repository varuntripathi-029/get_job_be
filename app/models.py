"""Central model import point.

Alembic autogenerate only sees tables that have been imported by the time it
inspects `Base.metadata`. Importing every model here — and importing this module
from `alembic/env.py` — keeps that from silently missing tables.
"""

from app.auth.models import User
from app.companies.models import Company
from app.crawler.models import CrawlLog
from app.database import Base
from app.extraction.models import Event
from app.jobs.models import Job
from app.newsletter.models import NewsletterSubscriber
from app.resumes.models import Resume
from app.scoring.models import CompanyScore
from app.sources.models import Source

__all__ = [
    "Base",
    "Company",
    "CompanyScore",
    "CrawlLog",
    "Event",
    "Job",
    "NewsletterSubscriber",
    "Resume",
    "Source",
    "User",
]
