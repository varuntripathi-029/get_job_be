"""Async SQLAlchemy engine, session factory, and declarative base."""

import uuid as _stdlib_uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import uuid_utils.compat as _uuid_utils
from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug and not settings.is_production,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base for all ORM models."""

    type_annotation_map: dict[Any, Any] = {}


def uuid7() -> _stdlib_uuid.UUID:
    """Time-ordered UUIDv7 — better index locality than uuid4 under B-tree PKs."""
    return _stdlib_uuid.UUID(str(_uuid_utils.uuid7()))


class UUIDPrimaryKeyMixin:
    """Adds a UUIDv7 primary key."""

    id: Mapped[_stdlib_uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )


class TimestampMixin:
    """Adds created_at / updated_at, both timestamptz with server defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
