"""Per-IP rate limiting, in process memory.

Deliberately not Redis-backed. Upstash's free tier bills per command, and
spending one on every public request would dwarf the crawl pipeline's usage. The
tradeoff is that limits are per-process: two workers each allow the full quota,
and everything resets on restart. That is acceptable for stopping casual abuse
on a single free-tier instance, and it is not a security control.

Behind a proxy, `request.client.host` is the proxy's address unless the ASGI
server is run with `--proxy-headers` and a trusted-host list. Without that every
client shares one bucket, so check deployment before relying on this.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request

from app.common.exceptions import RateLimitedError

# Buckets are pruned when a key goes idle, otherwise a long-running process
# accumulates one list per IP it has ever seen.
_PRUNE_EVERY_SECONDS = 300


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_prune = time.monotonic()

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """True if the request is allowed, False if it exceeds the limit."""
        now = time.monotonic()

        recent = [t for t in self._requests[key] if now - t < window_seconds]
        self._requests[key] = recent

        if len(recent) >= max_requests:
            return False

        recent.append(now)
        self._maybe_prune(now, window_seconds)
        return True

    def _maybe_prune(self, now: float, window_seconds: int) -> None:
        """Drop keys with no requests inside the window."""
        if now - self._last_prune < _PRUNE_EVERY_SECONDS:
            return
        self._last_prune = now
        stale = [
            key
            for key, times in self._requests.items()
            if not times or now - times[-1] >= window_seconds
        ]
        for key in stale:
            del self._requests[key]

    def reset(self) -> None:
        """Clear all buckets. For tests."""
        self._requests.clear()


_limiter = InMemoryRateLimiter()


def _client_key(request: Request, scope: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{scope}:{host}"


def _guard(request: Request, scope: str, limit: int, window: int, message: str) -> None:
    if not _limiter.check(_client_key(request, scope), limit, window):
        raise RateLimitedError(message)


async def rate_limit_public(request: Request) -> None:
    """60 requests/minute per IP."""
    _guard(request, "pub", 60, 60, "Too many requests. Try again in a minute.")


async def rate_limit_auth(request: Request) -> None:
    """10 requests/minute per IP — slows credential stuffing against sign-in."""
    _guard(request, "auth", 10, 60, "Too many authentication attempts.")


async def rate_limit_search(request: Request) -> None:
    """30 searches/minute per IP — search is the most expensive public query."""
    _guard(request, "search", 30, 60, "Too many searches. Try again in a minute.")
