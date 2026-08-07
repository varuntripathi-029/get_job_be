"""Resume processing pipeline and match retrieval."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError, ValidationError
from app.config import settings
from app.extraction.service import parse_resume_with_llm
from app.jobs.schemas import JobResponse
from app.resumes import embeddings, matcher, parser
from app.resumes.models import Resume
from app.resumes.schemas import JobMatchResponse, ResumeResponse

logger = logging.getLogger(__name__)


async def get_resume(db: AsyncSession, user_id: uuid.UUID) -> Resume | None:
    return await db.scalar(select(Resume).where(Resume.user_id == user_id))


async def require_resume(db: AsyncSession, user_id: uuid.UUID) -> Resume:
    resume = await get_resume(db, user_id)
    if resume is None:
        raise NotFoundError("No resume uploaded yet.")
    return resume


async def delete_resume(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Remove the row entirely rather than blanking it — this is PII, and a
    deletion request should leave nothing behind."""
    await db.execute(delete(Resume).where(Resume.user_id == user_id))
    await db.commit()


async def process_resume(
    db: AsyncSession,
    user_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
) -> tuple[Resume, list[str]]:
    """Extract text, parse with the LLM, embed, and persist.

    Returns the resume and any non-fatal warnings worth showing the uploader.
    One resume per user: an upload replaces the previous one in place, which the
    UNIQUE constraint on `user_id` enforces regardless.
    """
    warnings: list[str] = []

    try:
        text = await parser.extract_text(file_bytes, filename)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing = await get_resume(db, user_id)

    # Re-uploading the same bytes is common (users re-submit to check it worked).
    # Skipping the LLM and embedding calls makes that free.
    if existing is not None and existing.file_hash == file_hash:
        logger.info("resume unchanged for user %s — skipping reprocessing", user_id)
        existing.expires_at = _expiry()
        await db.commit()
        await db.refresh(existing)
        return existing, ["This resume was already processed; nothing changed."]

    parsed, model_id = await parse_resume_with_llm(text)
    if model_id is None:
        warnings.append(
            "Automatic parsing was unavailable, so skills could not be extracted."
        )

    vector: list[float] | None = None
    embedding_text = embeddings.resume_embedding_text(
        parsed.skills, parsed.role_families, parsed.seniority, parsed.locations
    )
    if embedding_text:
        vector = await embeddings.generate_embedding(
            embedding_text, task_type=embeddings.TASK_QUERY
        )
    if vector is None:
        warnings.append(
            "Job matching is unavailable until an embedding can be generated."
        )

    resume = existing or Resume(user_id=user_id)
    resume.file_name = filename
    resume.file_hash = file_hash
    resume.raw_text = text
    resume.parsed_skills = parsed.skills
    resume.parsed_role_families = parsed.role_families
    resume.parsed_seniority = parsed.seniority
    resume.parsed_experience_years = parsed.experience_years
    resume.parsed_locations = parsed.locations
    resume.work_mode_preference = parsed.work_mode_preference
    resume.extraction_model = model_id
    resume.parsed_at = datetime.now(UTC)
    resume.expires_at = _expiry()
    # Only overwrite a stored vector when a new one was produced, so a transient
    # embedding outage does not wipe a working one.
    if vector is not None:
        resume.embedding = vector

    if existing is None:
        db.add(resume)
    await db.commit()
    await db.refresh(resume)

    logger.info(
        "processed resume for user %s — %d skills, embedding=%s",
        user_id,
        len(parsed.skills),
        vector is not None,
    )
    return resume, warnings


async def refresh_embedding(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[Resume, list[str]]:
    """Regenerate the embedding from already-parsed fields, without re-parsing."""
    resume = await require_resume(db, user_id)

    text = embeddings.resume_embedding_text(
        resume.parsed_skills,
        resume.parsed_role_families,
        resume.parsed_seniority,
        resume.parsed_locations,
    )
    if not text:
        return resume, ["No parsed skills to embed. Re-upload your resume."]

    vector = await embeddings.generate_embedding(
        text, task_type=embeddings.TASK_QUERY
    )
    if vector is None:
        return resume, ["Embedding generation is currently unavailable."]

    resume.embedding = vector
    await db.commit()
    await db.refresh(resume)
    return resume, []


async def get_matched_jobs(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    role_family: str | None = None,
    seniority: str | None = None,
    work_mode: str | None = None,
    limit: int = 20,
) -> list[JobMatchResponse]:
    """Rank open jobs against the user's resume."""
    resume = await require_resume(db, user_id)

    if resume.embedding is None:
        raise ValidationError(
            "Your resume has no embedding yet. Call /resumes/refresh-matches "
            "to generate one."
        )

    # An explicit filter overrides the resume's own inferred families; with no
    # filter, the resume's families narrow the candidate set.
    families = [role_family] if role_family else (resume.parsed_role_families or None)

    matches = await matcher.find_matching_jobs(
        db,
        list(resume.embedding),
        role_families=families,
        seniority=seniority,
        work_mode=work_mode,
        limit=limit,
    )

    responses: list[JobMatchResponse] = []
    for job, company, similarity in matches:
        payload = JobResponse.model_validate(job)
        payload.company_name = company.name
        payload.company_slug = company.slug
        responses.append(
            JobMatchResponse(
                job=payload,
                similarity_score=round(similarity, 4),
                match_reasons=matcher.explain_match(resume, job, similarity),
            )
        )
    return responses


def to_response(resume: Resume) -> ResumeResponse:
    payload = ResumeResponse.model_validate(resume)
    payload.has_embedding = resume.embedding is not None
    return payload


def _expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.resume_expiry_days)
