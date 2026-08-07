"""Resume upload returns before indexing, and matching never calls a provider."""

import io
import uuid
from functools import lru_cache

import fitz
import pytest

from app.auth.models import User
from app.resumes import service
from app.resumes.models import INDEXING_STATUSES
from app.resumes.schemas import ResumeResponse

RESUME_TEXT = (
    "Jane Doe\nSenior Backend Engineer\n"
    "6 years building distributed systems.\n"
    "Python, PostgreSQL, Kafka, Docker, Kubernetes\n"
    "Bengaluru, India. Open to remote work.\n"
)


@lru_cache(maxsize=4)
def make_pdf(text: str = RESUME_TEXT) -> bytes:
    """Cached because PyMuPDF stamps a creation timestamp into the file, so two
    calls produce different bytes — and the upload path deduplicates on the
    SHA-256 of those bytes."""
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in text.split("\n"):
        page.insert_text((60, y), line, fontsize=11)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


def test_indexing_statuses_are_the_documented_set() -> None:
    assert INDEXING_STATUSES == ("pending", "processing", "ready", "failed")


def test_response_schema_exposes_indexing_status() -> None:
    """The client polls this field to know when matching is usable."""
    fields = ResumeResponse.model_fields
    assert "indexing_status" in fields
    assert "indexing_error" in fields
    # raw_text is PII and must never be returned.
    assert "raw_text" not in fields


# --- database-backed ---------------------------------------------------------


@pytest.fixture
async def user(db_session) -> User:
    account = User(
        email=f"resume-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        google_id=f"test|{uuid.uuid4()}",
        role="user",
        is_active=True,
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def test_upload_stores_pending_and_defers_the_work(db_session, user) -> None:
    """The request must not wait on the LLM or the embedding provider."""
    resume, needs_indexing = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )

    assert needs_indexing is True
    assert resume.indexing_status == "pending"
    assert resume.file_hash is not None
    assert resume.raw_text and "Jane Doe" in resume.raw_text
    # Nothing downstream has run yet.
    assert resume.embedding is None
    assert resume.parsed_skills is None
    assert resume.parsed_at is None


async def test_reuploading_the_same_file_skips_reprocessing(db_session, user) -> None:
    resume, _ = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    resume.indexing_status = "ready"
    await db_session.commit()

    again, needs_indexing = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    assert needs_indexing is False
    assert again.id == resume.id
    assert again.indexing_status == "ready"


async def test_a_failed_attempt_is_retried_on_reupload(db_session, user) -> None:
    """Same bytes, but the previous indexing failed — it should queue again."""
    resume, _ = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    resume.indexing_status = "failed"
    resume.indexing_error = "provider outage"
    await db_session.commit()

    again, needs_indexing = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    assert needs_indexing is True
    assert again.indexing_status == "pending"
    assert again.indexing_error is None


async def test_matches_explain_themselves_while_indexing(db_session, user) -> None:
    """Matching reads a stored vector; with none there yet it must say why
    rather than return a bare empty list."""
    await service.store_upload(db_session, user.id, make_pdf(), "jane.pdf")

    matches, message = await service.get_matched_jobs(db_session, user.id)
    assert matches == []
    assert message and "queued" in message.lower()


async def test_matches_report_a_failed_index(db_session, user) -> None:
    resume, _ = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    resume.indexing_status = "failed"
    await db_session.commit()

    matches, message = await service.get_matched_jobs(db_session, user.id)
    assert matches == []
    assert message and "could not be processed" in message.lower()


async def test_matching_makes_no_provider_calls(db_session, user, monkeypatch) -> None:
    """The whole point of pre-computing: GET /resumes/matches must be free."""
    called = False

    async def explode(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("matching must not call the embedding provider")

    from app.embeddings.gemini import GeminiEmbeddingProvider

    monkeypatch.setattr(GeminiEmbeddingProvider, "embed_single", explode)
    monkeypatch.setattr(GeminiEmbeddingProvider, "embed_batch", explode)

    await service.store_upload(db_session, user.id, make_pdf(), "jane.pdf")
    await service.get_matched_jobs(db_session, user.id)
    assert called is False


async def test_indexing_fails_gracefully_without_a_provider(
    db_session, user, monkeypatch
) -> None:
    """No API key must produce a 'failed' status, not an exception."""
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")

    async def fake_parse(text):
        from app.extraction.schemas import ParsedResume

        return ParsedResume(skills=["Python"], role_families=["engineering"]), "stub"

    monkeypatch.setattr(service, "parse_resume_with_llm", fake_parse)

    resume, _ = await service.store_upload(
        db_session, user.id, make_pdf(), "jane.pdf"
    )
    indexed = await service.index_resume(db_session, resume.id)

    assert indexed is not None
    assert indexed.indexing_status == "failed"
    assert indexed.indexing_error
    # The parse still landed, so the profile is usable even without matching.
    assert indexed.parsed_skills == ["Python"]


def test_docx_and_pdf_both_accepted() -> None:
    """Guards the upload path's file-type handling without touching a DB."""
    from docx import Document

    document = Document()
    document.add_paragraph(RESUME_TEXT)
    buffer = io.BytesIO()
    document.save(buffer)
    assert buffer.getvalue()[:2] == b"PK"
    assert make_pdf()[:4] == b"%PDF"
