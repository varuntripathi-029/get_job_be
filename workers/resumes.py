"""Resume indexing — LLM parse plus embedding, off the request path."""

from __future__ import annotations

import logging
import uuid

from app.resumes.service import index_resume as _index_resume
from workers.base import run_async, with_session
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="workers.resumes.index_resume", bind=True, max_retries=2)
def index_resume(self, resume_id: str) -> dict[str, object]:
    """Parse and embed one uploaded resume.

    Failures are recorded on the row as indexing_status='failed' rather than
    raised, so the user sees a status instead of a task silently disappearing.
    """

    async def run(db):
        resume = await _index_resume(db, uuid.UUID(resume_id))
        if resume is None:
            return {"resume_id": resume_id, "status": "missing"}
        return {
            "resume_id": resume_id,
            "status": resume.indexing_status,
            "skills": len(resume.parsed_skills or []),
        }

    return run_async(with_session(run))
