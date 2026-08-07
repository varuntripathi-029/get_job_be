"""Resume routes — all authenticated, all scoped to the calling user."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.common.exceptions import ValidationError
from app.config import settings
from app.database import get_db
from app.jobs.models import ROLE_FAMILIES, SENIORITIES, WORK_MODES
from app.resumes import service
from app.resumes.schemas import (
    JobMatchesResponse,
    ResumeResponse,
    ResumeUploadResponse,
)

router = APIRouter(prefix="/resumes", tags=["resumes"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/upload", response_model=ResumeUploadResponse)
async def upload_resume(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> ResumeUploadResponse:
    """Upload a PDF or DOCX resume. Replaces any previous upload.

    Returns as soon as the file is stored and validated. Parsing and embedding
    run in a worker — poll `GET /resumes/me` until `indexing_status` is
    `ready`.
    """
    # Starlette buffers to disk past a threshold, so this read is bounded by the
    # size check below rather than by memory. content_type is not trusted — it
    # comes from the client — so the real check is on the parsed bytes.
    file_bytes = await file.read()
    if len(file_bytes) > settings.max_resume_size_bytes:
        raise ValidationError(
            f"File is too large. The limit is {settings.max_resume_size_mb}MB."
        )

    resume, needs_indexing = await service.store_upload(
        db, user.id, file_bytes, file.filename or "resume"
    )

    if needs_indexing:
        # Imported here so the API does not depend on the worker package at
        # import time; a broker outage must not stop the app from booting.
        from workers.resumes import index_resume

        index_resume.delay(str(resume.id))
        message = "Resume received. Parsing and matching are being prepared."
    else:
        message = "This resume was already processed; nothing changed."

    return ResumeUploadResponse(resume=service.to_response(resume), message=message)


@router.get("/me", response_model=ResumeResponse)
async def get_my_resume(user: CurrentUser, db: DbSession) -> ResumeResponse:
    return service.to_response(await service.require_resume(db, user.id))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_resume(user: CurrentUser, db: DbSession) -> None:
    await service.delete_resume(db, user.id)


@router.get("/matches", response_model=JobMatchesResponse)
async def get_matches(
    user: CurrentUser,
    db: DbSession,
    role_family: Annotated[str | None, Query(pattern="|".join(ROLE_FAMILIES))] = None,
    seniority: Annotated[str | None, Query(pattern="|".join(SENIORITIES))] = None,
    work_mode: Annotated[str | None, Query(pattern="|".join(WORK_MODES))] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobMatchesResponse:
    """Jobs ranked against the stored resume vector.

    Reads a pre-computed embedding; no provider call happens here.
    """
    matches, message = await service.get_matched_jobs(
        db,
        user.id,
        role_family=role_family,
        seniority=seniority,
        work_mode=work_mode,
        limit=limit,
    )
    return JobMatchesResponse(matches=matches, count=len(matches), message=message)


@router.post("/refresh-matches", response_model=ResumeUploadResponse)
async def refresh_matches(user: CurrentUser, db: DbSession) -> ResumeUploadResponse:
    """Re-run parsing and embedding for the stored resume."""
    resume = await service.require_resume(db, user.id)
    resume.indexing_status = "pending"
    resume.indexing_error = None
    await db.commit()
    await db.refresh(resume)

    from workers.resumes import index_resume

    index_resume.delay(str(resume.id))
    return ResumeUploadResponse(
        resume=service.to_response(resume),
        message="Re-indexing started. Poll /resumes/me until it is ready.",
    )
