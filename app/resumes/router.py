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
    JobMatchResponse,
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
    """Upload a PDF or DOCX resume. Replaces any previous upload."""
    # Starlette buffers to disk past a threshold, so this read is bounded by the
    # size check below rather than by memory. content_type is not trusted — it
    # comes from the client — so the real check is on the parsed bytes.
    file_bytes = await file.read()
    if len(file_bytes) > settings.max_resume_size_bytes:
        raise ValidationError(
            f"File is too large. The limit is {settings.max_resume_size_mb}MB."
        )

    resume, warnings = await service.process_resume(
        db, user.id, file_bytes, file.filename or "resume"
    )

    matches = []
    if resume.embedding is not None:
        matches = await service.get_matched_jobs(db, user.id, limit=20)

    return ResumeUploadResponse(
        resume=service.to_response(resume),
        matched_job_count=len(matches),
        warnings=warnings,
    )


@router.get("/me", response_model=ResumeResponse)
async def get_my_resume(user: CurrentUser, db: DbSession) -> ResumeResponse:
    return service.to_response(await service.require_resume(db, user.id))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_resume(user: CurrentUser, db: DbSession) -> None:
    await service.delete_resume(db, user.id)


@router.get("/matches", response_model=list[JobMatchResponse])
async def get_matches(
    user: CurrentUser,
    db: DbSession,
    role_family: Annotated[str | None, Query(pattern="|".join(ROLE_FAMILIES))] = None,
    seniority: Annotated[str | None, Query(pattern="|".join(SENIORITIES))] = None,
    work_mode: Annotated[str | None, Query(pattern="|".join(WORK_MODES))] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobMatchResponse]:
    return await service.get_matched_jobs(
        db,
        user.id,
        role_family=role_family,
        seniority=seniority,
        work_mode=work_mode,
        limit=limit,
    )


@router.post("/refresh-matches", response_model=ResumeUploadResponse)
async def refresh_matches(user: CurrentUser, db: DbSession) -> ResumeUploadResponse:
    """Regenerate the resume embedding and re-run matching."""
    resume, warnings = await service.refresh_embedding(db, user.id)

    matches = []
    if resume.embedding is not None:
        matches = await service.get_matched_jobs(db, user.id, limit=20)

    return ResumeUploadResponse(
        resume=service.to_response(resume),
        matched_job_count=len(matches),
        warnings=warnings,
    )
