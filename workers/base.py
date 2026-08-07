"""Bridge between Celery's synchronous tasks and the async data layer.

Celery workers are not async. Each task opens its own event loop and its own
session; the request-scoped `get_db` dependency does not apply here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal


def run_async[T](coro: Awaitable[T]) -> T:
    """Run a coroutine to completion from synchronous task code."""
    return asyncio.run(coro)  # type: ignore[arg-type]


async def with_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `fn` with a session that is always closed, and rolled back on error."""
    async with AsyncSessionLocal() as session:
        try:
            return await fn(session)
        except Exception:
            await session.rollback()
            raise
