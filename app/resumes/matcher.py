"""Job ↔ resume matching via pgvector, plus human-readable match reasons."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company
from app.jobs.models import Job
from app.resumes.models import Resume

logger = logging.getLogger(__name__)

# Below this, "matches" are noise — cosine similarity on 768-dim embeddings puts
# unrelated technical text around 0.4-0.5, so anything under this is not a match
# in any sense a user would recognise.
MIN_SIMILARITY = 0.55


async def find_matching_jobs(
    db: AsyncSession,
    resume_embedding: list[float],
    *,
    role_families: list[str] | None = None,
    seniority: str | None = None,
    work_mode: str | None = None,
    min_similarity: float = MIN_SIMILARITY,
    limit: int = 20,
) -> list[tuple[Job, Company, float]]:
    """Rank active jobs by cosine similarity to a resume vector.

    `<=>` is pgvector's cosine distance, so similarity is `1 - distance`.
    Ordering is on distance rather than the derived similarity so pgvector can
    use an index on the column when one is added.
    """
    distance = Job.embedding.cosine_distance(resume_embedding)
    similarity = (1 - distance).label("similarity")

    stmt = (
        select(Job, Company, similarity)
        .join(Company, Company.id == Job.company_id)
        .where(Job.is_active.is_(True), Job.embedding.is_not(None))
    )

    # Filters narrow the candidate set before ranking. role_family is a list
    # because a resume can legitimately span several (data + engineering).
    if role_families:
        stmt = stmt.where(Job.role_family.in_(role_families))
    if seniority:
        stmt = stmt.where(Job.seniority == seniority)
    if work_mode:
        stmt = stmt.where(Job.work_mode == work_mode)
    if min_similarity > 0:
        stmt = stmt.where(similarity >= min_similarity)

    result = await db.execute(stmt.order_by(distance).limit(limit))
    return [(job, company, float(score)) for job, company, score in result.all()]


def _normalize(values: list[str] | None) -> set[str]:
    return {v.strip().lower() for v in (values or []) if v and v.strip()}


# Years of experience that make each level a plausible fit. Used only to phrase
# a reason, never to filter — a candidate is free to apply anywhere.
_SENIORITY_YEARS = {
    "intern": (0, 1),
    "junior": (0, 2),
    "mid": (2, 5),
    "senior": (5, 9),
    "staff": (8, 14),
    "principal": (10, 20),
    "director": (10, 25),
    "vp": (12, 30),
    "c_level": (15, 40),
}


def explain_match(resume: Resume, job: Job, similarity: float) -> list[str]:
    """Explain why a job surfaced, in the user's terms.

    Pure keyword and field comparison — no LLM. These strings appear next to
    every match, so an LLM call here would multiply cost by the result count for
    something string formatting handles correctly.
    """
    reasons: list[str] = []

    shared = _normalize(resume.parsed_skills) & _normalize(job.skills)
    if shared:
        # Preserve the resume's own casing rather than the normalised form.
        display = [
            s for s in (resume.parsed_skills or []) if s.strip().lower() in shared
        ]
        shown = ", ".join(display[:4])
        if len(display) > 4:
            shown += f", +{len(display) - 4} more"
        noun = "skill" if len(display) == 1 else "skills"
        reasons.append(f"You have {len(display)} matching {noun}: {shown}")

    if job.role_family and job.role_family in _normalize(resume.parsed_role_families):
        reasons.append(f"This {job.role_family} role matches your experience")

    if job.seniority:
        years = resume.parsed_experience_years
        if job.seniority == resume.parsed_seniority:
            label = job.seniority.replace("_", " ").title()
            reasons.append(f"{label} level matches your profile")
        elif years is not None:
            low, high = _SENIORITY_YEARS.get(job.seniority, (0, 100))
            if low <= years <= high:
                label = job.seniority.replace("_", " ").title()
                plural = "year" if years == 1 else "years"
                reasons.append(
                    f"{label} level matches your {years:g} {plural} of experience"
                )

    job_location = (job.location_normalized or job.location_raw or "").strip()
    if job_location:
        location_l = job_location.lower()
        for candidate in resume.parsed_locations or []:
            if candidate.strip() and candidate.strip().lower() in location_l:
                reasons.append(f"This role is in {job_location}, where you're based")
                break

    if job.work_mode and job.work_mode == resume.work_mode_preference:
        reasons.append(f"{job.work_mode.title()} work matches your preference")

    if not reasons:
        # Never return an empty list — the vector still found this similar, and
        # saying so is more honest than showing a match with no explanation.
        reasons.append(
            f"Your profile is a {similarity:.0%} semantic match for this role"
        )

    return reasons
