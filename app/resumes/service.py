"""Resume processing and match retrieval.

Upload is deliberately split in two. The request extracts text, stores the row
and returns; the LLM parse and the embedding — together several seconds of
network latency, and the LLM alone was measured at ~14s on a cold model — run in
a worker. `indexing_status` is how the client learns when matching is ready.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError, ValidationError
from app.config import settings
from app.embeddings import (
    TASK_QUERY,
    build_resume_embedding_text,
    embeddings_available,
    get_provider,
)
from app.extraction.service import parse_resume_with_llm
from app.jobs.schemas import JobResponse
from app.resumes import matcher, parser
from app.resumes.models import Resume
from app.resumes.schemas import JobMatchResponse, ResumeResponse

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "pending": "Your resume is queued for processing. Matches appear shortly.",
    "processing": "Your resume is being processed. Matches appear shortly.",
    "failed": (
        "Your resume could not be processed for matching. Try uploading again."
    ),
}


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


async def store_upload(
    db: AsyncSession, user_id: uuid.UUID, file_bytes: bytes, filename: str
) -> tuple[Resume, bool]:
    """Extract text and persist the row. Returns `(resume, needs_indexing)`.

    Does no LLM or embedding work — that is the caller's job to dispatch, so
    the HTTP request returns as soon as the file is safely stored.
    """
    try:
        text = await parser.extract_text(file_bytes, filename)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing = await get_resume(db, user_id)

    # Re-uploading the same bytes is common (people resubmit to check it
    # worked). Nothing downstream would change, so skip the paid work entirely.
    # A previously failed attempt on the same file still gets retried, so the
    # skip only applies once indexing actually succeeded.
    if (
        existing is not None
        and existing.file_hash == file_hash
        and existing.indexing_status == "ready"
    ):
        existing.expires_at = _expiry()
        await db.commit()
        await db.refresh(existing)
        logger.info("resume unchanged for user %s — reusing", user_id)
        return existing, False

    resume = existing or Resume(user_id=user_id)
    resume.file_name = filename
    resume.file_hash = file_hash
    resume.raw_text = text
    resume.indexing_status = "pending"
    resume.indexing_error = None
    resume.expires_at = _expiry()

    if existing is None:
        db.add(resume)
    await db.commit()
    await db.refresh(resume)

    logger.info("stored resume for user %s (%d chars), queued", user_id, len(text))
    return resume, True


async def index_resume(db: AsyncSession, resume_id: uuid.UUID) -> Resume | None:
    """Parse and embed a stored resume. Runs in a worker, never in a request."""
    resume = await db.get(Resume, resume_id)
    if resume is None:
        logger.warning("resume %s no longer exists — nothing to index", resume_id)
        return None

    if not resume.raw_text:
        return await _fail(db, resume, "No text was extracted from the upload.")

    resume.indexing_status = "processing"
    await db.commit()

    try:
        parsed, model_id = await parse_resume_with_llm(resume.raw_text)
    except Exception as exc:  # noqa: BLE001 — one bad parse must not kill the worker
        logger.exception("resume parse crashed for %s", resume_id)
        return await _fail(db, resume, f"Parsing failed: {exc}")

    resume.parsed_skills = parsed.skills
    resume.parsed_role_families = parsed.role_families
    resume.parsed_seniority = parsed.seniority
    resume.parsed_experience_years = parsed.experience_years
    resume.parsed_locations = parsed.locations
    resume.work_mode_preference = parsed.work_mode_preference
    resume.extraction_model = model_id
    resume.parsed_at = datetime.now(UTC)

    if not embeddings_available():
        await db.commit()
        return await _fail(
            db, resume, "Embeddings are not configured, so matching is unavailable."
        )

    text = build_resume_embedding_text(
        parsed.skills, parsed.role_families, parsed.seniority, parsed.locations
    )
    if not text:
        await db.commit()
        return await _fail(db, resume, "No skills could be extracted to match on.")

    # A resume is the query side of the retrieval pair, not a document.
    vector = await get_provider().embed_single(text, task_type=TASK_QUERY)
    if vector is None:
        await db.commit()
        return await _fail(db, resume, "The embedding could not be generated.")

    resume.embedding = vector
    resume.indexing_status = "ready"
    resume.indexing_error = None
    await db.commit()
    await db.refresh(resume)

    logger.info(
        "indexed resume %s — %d skills, embedding ready", resume_id, len(parsed.skills)
    )
    return resume


async def _fail(db: AsyncSession, resume: Resume, reason: str) -> Resume:
    resume.indexing_status = "failed"
    resume.indexing_error = reason[:500]
    await db.commit()
    await db.refresh(resume)
    logger.warning("resume %s indexing failed: %s", resume.id, reason)
    return resume


async def get_matched_jobs(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    role_family: str | None = None,
    seniority: str | None = None,
    work_mode: str | None = None,
    limit: int = 20,
) -> tuple[list[JobMatchResponse], str | None]:
    """Rank open jobs against the user's stored resume vector.

    Reads the pre-computed embedding and does nothing else — no provider call
    happens on this path, so matching stays fast and free however often it is
    called. Returns `(matches, message)`; the message explains an empty list.
    """
    resume = await require_resume(db, user_id)

    if resume.embedding is None:
        return [], STATUS_MESSAGES.get(
            resume.indexing_status,
            "Your resume has no embedding yet, so matching is unavailable.",
        )

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

    message = None if responses else "No open roles matched your profile yet."
    return responses, message


def to_response(resume: Resume) -> ResumeResponse:
    payload = ResumeResponse.model_validate(resume)
    payload.has_embedding = resume.embedding is not None
    return payload


def _expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.resume_expiry_days)
