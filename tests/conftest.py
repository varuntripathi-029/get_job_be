"""Shared pytest fixtures."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Keep tests off any real credentials before app.config is imported.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-used-in-production")

from app.database import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://hiresignal:hiresignal@localhost:5432/hiresignal_test",
)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    """HTTP client for routes that do not touch the database."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_engine():
    """Engine against a throwaway test database.

    Skips rather than fails when no Postgres is reachable, so the unit tests
    still run on a machine with nothing started.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"No test database available: {exc}")

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def db_client(db_engine) -> AsyncGenerator[AsyncClient]:
    """HTTP client wired to the test database."""
    app = create_app()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
