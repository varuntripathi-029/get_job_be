"""ATS job synchronisation.

Jobs are a timeline, not a snapshot. A posting that disappears from a board is
closed, never deleted, so "posted 40 roles last quarter" stays answerable long
after those roles filled. That history is also what makes hiring momentum
measurable rather than just a current headcount.

Classification is rule-based. Job titles are a small, highly conventional
vocabulary, and running an LLM over thousands of them would cost real money to
do worse than a keyword table.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.crawler.fetchers.ats import ATSFetcher
from app.jobs.models import Job
from app.sources.models import Source

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncResult:
    new: int = 0
    updated: int = 0
    closed: int = 0
    unchanged: int = 0

    @property
    def total_seen(self) -> int:
        return self.new + self.updated + self.unchanged


# Ordered: the first match wins, so the most specific patterns come first.
# "data engineer" must beat "engineer", and "platform engineer" must beat both.
ROLE_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data",
        (
            "data scientist", "data engineer", "data analyst", "machine learning",
            "ml engineer", "ai engineer", "analytics", "bi analyst", "data science",
            "deep learning", "nlp engineer",
        ),
    ),
    (
        "devops",
        (
            "devops", "sre", "site reliability", "infrastructure engineer",
            "cloud engineer", "platform engineer", "reliability engineer",
        ),
    ),
    # Design sits ahead of product deliberately: "Product Designer" is a design
    # role, and whichever family matches first wins.
    (
        "design",
        (
            "designer", "design", "ux", "ui", "user experience",
            "user interface", "creative director",
        ),
    ),
    (
        "product",
        (
            "product manager", "program manager", "product owner", "tpm",
            "technical program", "product analyst", "product",
        ),
    ),
    (
        "engineering",
        (
            "engineer", "engineering", "developer", "development", "swe", "sde",
            "architect", "backend", "back-end", "frontend", "front-end",
            "fullstack", "full-stack", "full stack", "embedded", "firmware",
            "qa", "test engineer", "automation", "programmer", "technology",
            "technical",
        ),
    ),
    (
        "marketing",
        (
            "marketing", "growth", "content", "brand", "seo", "social media",
            "communications", "copywriter",
        ),
    ),
    (
        "sales",
        (
            "sales", "account executive", "business development", "bdr", "sdr",
            "partnerships", "account manager",
        ),
    ),
    (
        "hr",
        (
            "recruiter", "recruiting", "recruitment", "people", "talent",
            "human resource", "hr",
        ),
    ),
    (
        "finance",
        ("finance", "financial", "accounting", "accountant", "controller",
         "treasury", "fp&a"),
    ),
    ("legal", ("legal", "counsel", "compliance", "regulatory")),
    (
        "operations",
        ("operations", "operation", "supply chain", "logistics", "procurement",
         "ops"),
    ),
)

# Ordered most senior first: "senior director" is a director, not a senior.
SENIORITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("c_level", ("cto", "ceo", "cfo", "coo", "cmo", "chief")),
    ("vp", ("vp", "vice president", "svp", "evp")),
    ("director", ("director",)),
    ("principal", ("principal", "distinguished", "fellow")),
    ("staff", ("staff",)),
    ("intern", ("intern", "trainee", "apprentice", "co-op", "graduate trainee")),
    ("junior", ("junior", "jr", "jr.", "entry level", "fresher")),
    ("senior", ("senior", "sr", "sr.", "lead", "tech lead")),
)

WORK_MODE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "remote",
        (
            "fully remote", "100% remote", "work from anywhere", "remote-first",
            "remote first", "wfh", "work from home",
        ),
    ),
    (
        "hybrid",
        ("hybrid", "2 days in office", "3 days in office", "flexible working"),
    ),
    (
        "onsite",
        (
            "onsite", "on-site", "in-office", "office-based", "in office",
            "work from office",
        ),
    ),
)


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[tuple[str, re.Pattern[str]], ...], ...]:
    """Word-boundary patterns for each rule table.

    Boundaries are essential, not cosmetic. With plain substring matching every
    "Director of Product" is classified c_level, because "dire(cto)r" contains
    "cto". Short acronyms are only safe as whole words.

    A boundary is only added on a side where the keyword actually ends in a word
    character: r"\b" never matches after the "." in "sr." or the "&" in "fp&a",
    so applying it unconditionally would break exactly the keywords that need it.
    """

    def build(rules):
        compiled = []
        for label, keywords in rules:
            parts = []
            for keyword in keywords:
                prefix = r"\b" if keyword[0].isalnum() else ""
                suffix = r"\b" if keyword[-1].isalnum() else ""
                parts.append(prefix + re.escape(keyword) + suffix)
            compiled.append((label, re.compile("|".join(parts), re.I)))
        return tuple(compiled)

    return build(ROLE_FAMILY_RULES), build(SENIORITY_RULES), build(WORK_MODE_RULES)


def classify_job_title(title: str) -> tuple[str | None, str | None]:
    """Infer `(role_family, seniority)` from a job title."""
    if not title:
        return None, None
    haystack = title.lower().strip()
    role_rules, seniority_rules, _ = _compiled()

    role_family = "other"
    for family, pattern in role_rules:
        if pattern.search(haystack):
            role_family = family
            break

    seniority = None
    for level, pattern in seniority_rules:
        if pattern.search(haystack):
            seniority = level
            break
    # No marker at all means an ordinary individual-contributor role, which is
    # what "mid" means here.
    if seniority is None:
        seniority = "mid"

    return role_family, seniority


def detect_work_mode(
    description: str | None, location: str | None = None
) -> str | None:
    haystack = f"{description or ''} {location or ''}".lower()
    if not haystack.strip():
        return None
    _, _, mode_rules = _compiled()
    for mode, pattern in mode_rules:
        if pattern.search(haystack):
            return mode
    return None


def content_hash(description: str | None) -> str:
    return hashlib.sha256((description or "").encode("utf-8", "replace")).hexdigest()


def _apply(job: Job, payload: dict) -> None:
    """Set the derived fields on a Job from a normalised ATS payload."""
    job.title = payload["title"]
    job.description_text = payload.get("description_text") or None
    job.department = payload.get("department")
    job.location_raw = payload.get("location_raw")
    job.application_url = payload.get("application_url")
    job.role_family, job.seniority = classify_job_title(payload["title"])
    job.work_mode = detect_work_mode(
        payload.get("description_text"), payload.get("location_raw")
    )
    job.content_hash = content_hash(payload.get("description_text"))


async def sync_company_jobs(
    db: AsyncSession,
    company: Company,
    source: Source,
    *,
    fetcher: ATSFetcher | None = None,
) -> SyncResult:
    """Diff a company's ATS board against what is stored."""
    fetcher = fetcher or ATSFetcher()
    postings = await fetcher.fetch_jobs(source.url)

    if not postings:
        logger.info("no postings returned for %s (%s)", company.slug, source.url)
        return SyncResult()

    # An ATS API returns every open posting in one response, so anything absent
    # really is closed. That is not true of the scraped path — see allow_close.
    return await reconcile_jobs(db, company, source, postings)


async def reconcile_jobs(
    db: AsyncSession,
    company: Company,
    source: Source,
    postings: list[dict],
    *,
    allow_close: bool = True,
) -> SyncResult:
    """Diff a set of normalised postings against what is stored.

    `allow_close` exists because the close pass is only safe when the input is
    authoritative. An ATS API either returns the whole board or errors, so a
    missing posting means a closed role. A scraped careers page can return a
    partial list for a dozen boring reasons — a slow render, a layout change, a
    truncated LLM response — and closing on that would retract dozens of live
    roles from one bad parse.
    """
    result = SyncResult()

    # Keep the last occurrence if a board somehow repeats an id.
    incoming = {
        str(p["external_id"]): p
        for p in postings
        if p.get("external_id") and p.get("title")
    }

    # Scoped to the source, not just the company. A company can have both an
    # ATS board and a scraped careers page, and they must never diff against
    # each other's rows — the scrape would see every ATS job as "missing".
    existing_rows = list(
        (
            await db.execute(
                select(Job).where(
                    Job.company_id == company.id,
                    Job.source_id == source.id,
                    Job.external_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    existing = {job.external_id: job for job in existing_rows}

    now = datetime.now(UTC)
    incoming_ids = set(incoming)
    existing_ids = set(existing)

    for external_id in incoming_ids - existing_ids:
        payload = incoming[external_id]
        job = Job(
            company_id=company.id,
            source_id=source.id,
            external_id=external_id,
            title=payload["title"],
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        _apply(job, payload)
        db.add(job)
        result.new += 1

    for external_id in incoming_ids & existing_ids:
        job = existing[external_id]
        payload = incoming[external_id]
        job.last_seen_at = now

        # A posting that vanished and came back is open again.
        if not job.is_active:
            job.is_active = True
            job.closed_at = None

        if job.content_hash != content_hash(payload.get("description_text")):
            _apply(job, payload)
            result.updated += 1
        else:
            result.unchanged += 1

    if allow_close:
        for external_id in existing_ids - incoming_ids:
            job = existing[external_id]
            if job.is_active:
                job.is_active = False
                job.closed_at = now
                result.closed += 1
    elif existing_ids - incoming_ids:
        logger.info(
            "%s: %d stored jobs absent from this scrape, left open "
            "(close pass disabled for non-authoritative sources)",
            company.slug,
            len(existing_ids - incoming_ids),
        )

    logger.info(
        "%s: %d new, %d updated, %d closed, %d unchanged",
        company.slug,
        result.new,
        result.updated,
        result.closed,
        result.unchanged,
    )
    return result
