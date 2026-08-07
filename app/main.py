"""FastAPI application factory."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.common.exceptions import AppError
from app.companies.router import router as companies_router
from app.config import settings
from app.dashboard.router import router as dashboard_router
from app.database import engine
from app.extraction.router import router as events_router
from app.jobs.router import router as jobs_router
from app.newsletter.router import router as newsletter_router
from app.resumes.router import router as resumes_router
from app.scoring.router import router as scoring_router
from app.search.router import router as search_router
from app.sources.router import router as sources_router

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

VERSION = "0.1.0"

DESCRIPTION = """
AI hiring intelligence for Indian startups.

HireSignal watches public signals — ATS boards, career pages, funding news,
engineering blogs — and turns them into an explainable **hiring momentum score**
for each company.

**It never claims a company will hire.** The strongest supported claim is that a
company shows strong recent hiring signals, and every score links to the
evidence behind it.

Most read endpoints are public and need no authentication. Sign-in is Google
OAuth only; send the Google ID token to `POST /auth/google` and use the returned
access token as `Authorization: Bearer <token>`.
"""

TAGS_METADATA = [
    {"name": "meta", "description": "Health and service metadata."},
    {"name": "auth", "description": "Google OAuth sign-in and token refresh."},
    {
        "name": "companies",
        "description": "Company profiles, filtering, and side-by-side comparison.",
    },
    {"name": "jobs", "description": "Open roles synced from company ATS boards."},
    {
        "name": "events",
        "description": "Hiring signals extracted from public sources, with evidence.",
    },
    {"name": "scores", "description": "Momentum score history for charting."},
    {
        "name": "search",
        "description": "Search across companies, jobs and events in one request.",
    },
    {
        "name": "dashboard",
        "description": "Public aggregates for the landing page. Cached server-side.",
    },
    {
        "name": "resumes",
        "description": "Resume upload, parsing, and vector job matching. "
        "Resumes are PII and expire automatically.",
    },
    {"name": "newsletter", "description": "Double opt-in weekly digest."},
    {"name": "sources", "description": "What is tracked, and submitting new sources."},
    {"name": "admin", "description": "Admin-only moderation and operations."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    logger.info("starting %s env=%s", settings.app_name, settings.environment)
    if not settings.google_client_id:
        logger.warning("GOOGLE_CLIENT_ID is unset — /auth/google will reject requests.")
    if not settings.embeddings_enabled:
        logger.warning(
            "no API key for embedding provider %r — resume matching and job "
            "embeddings are disabled",
            settings.embedding_provider,
        )
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY is unset — no newsletter email will send.")
    if missing := settings.missing_llm_keys:
        logger.warning(
            "no API key for LLM provider(s) %s — extraction is disabled "
            "(classifier=%s/%s, extractor=%s/%s)",
            ", ".join(missing),
            settings.classifier_provider,
            settings.classifier_model,
            settings.extractor_provider,
            settings.extractor_model,
        )
    yield
    await engine.dispose()
    logger.info("shutdown complete")


async def _check_database() -> tuple[str, str | None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any failure is the same signal
        logger.warning("health: database check failed: %s", exc)
        return "error", str(exc)[:200]
    return "ok", None


async def _check_redis() -> tuple[str, str | None]:
    if not settings.redis_url:
        return "unavailable", "REDIS_URL is not configured"
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, socket_connect_timeout=5)
        try:
            await client.ping()
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("health: redis check failed: %s", exc)
        return "unavailable", str(exc)[:200]
    return "ok", None


def create_app() -> FastAPI:
    app = FastAPI(
        title="HireSignal API",
        version=VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/health", tags=["meta"], summary="Liveness and dependency status")
    async def health() -> JSONResponse:
        """Reports the app plus its dependencies.

        Returns 503 only when the database is unreachable — without it nothing
        works. A missing Redis degrades background work and rate limiting but
        leaves every read endpoint functional, so it stays a 200.
        """
        db_status, db_error = await _check_database()
        redis_status, redis_error = await _check_redis()

        dependencies: dict[str, object] = {
            "database": db_status,
            "redis": redis_status,
        }
        if db_error:
            dependencies["database_error"] = db_error
        if redis_error:
            dependencies["redis_error"] = redis_error

        healthy = db_status == "ok" and redis_status == "ok"
        body = {
            "status": "ok" if healthy else "degraded",
            "version": VERSION,
            "dependencies": dependencies,
        }
        return JSONResponse(
            status_code=503 if db_status == "error" else 200, content=body
        )

    for router in (
        auth_router,
        companies_router,
        sources_router,
        jobs_router,
        events_router,
        search_router,
        dashboard_router,
        resumes_router,
        newsletter_router,
        scoring_router,
        admin_router,
    ):
        app.include_router(router)

    return app


app = create_app()
