"""Bridge between Celery's synchronous tasks and the async data layer.

Celery workers are not async. Each task opens its own event loop and its own
session; the request-scoped `get_db` dependency does not apply here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

# Registers every model on Base.metadata. A task module that imports only the
# one model it touches leaves sibling foreign keys unresolvable, and the first
# flush dies with NoReferencedTableError — which only shows up when a task is
# invoked standalone rather than through a worker that imported everything.
import app.models  # noqa: F401
from app.database import AsyncSessionLocal, engine


def run_async[T](coro: Awaitable[T]) -> T:
    """Run a coroutine to completion from synchronous task code.

    The engine is disposed before the loop closes. `asyncio.run` builds a fresh
    event loop per call, but the engine is module-level and its pool outlives
    it — so the second task in a worker process would check out a connection
    bound to a loop that no longer exists and die on `pool_pre_ping` with
    "Event loop is closed". Reconnecting per task costs one handshake; carrying
    a dead connection costs every task after the first.
    """

    async def _run_and_dispose() -> T:
        try:
            return await coro  # type: ignore[misc]
        finally:
            await engine.dispose()

    return asyncio.run(_run_and_dispose())


async def with_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `fn` with a session that is always closed, and rolled back on error."""
    async with AsyncSessionLocal() as session:
        try:
            return await fn(session)
        except Exception:
            await session.rollback()
            raise
