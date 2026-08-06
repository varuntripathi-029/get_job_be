"""FastAPI application factory."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.common.exceptions import AppError
from app.companies.router import router as companies_router
from app.config import settings
from app.database import engine
from app.jobs.router import router as jobs_router
from app.newsletter.router import router as newsletter_router
from app.resumes.router import router as resumes_router
from app.scoring.router import router as scoring_router
from app.sources.router import router as sources_router

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    logger.info("starting %s env=%s", settings.app_name, settings.environment)
    if not settings.google_client_id:
        logger.warning("GOOGLE_CLIENT_ID is unset — /auth/google will reject requests.")
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY is unset — LLM extraction is disabled.")
    yield
    await engine.dispose()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        description="AI hiring intelligence platform.",
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
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    for router in (
        auth_router,
        companies_router,
        sources_router,
        jobs_router,
        resumes_router,
        newsletter_router,
        scoring_router,
        admin_router,
    ):
        app.include_router(router)

    return app


app = create_app()
