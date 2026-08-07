"""Per-domain politeness limiter.

Keyed on the registrable domain, not the hostname: `boards-api.greenhouse.io`
and `api.greenhouse.io` are one operator's infrastructure, and hammering both at
once is exactly what a limiter is meant to prevent.

Redis-backed with an in-memory fallback. The Redis path is a single
`SET key 1 NX EX n` — one Upstash command per check, which is the cheapest
possible correct implementation. Lua scripts and pipelines cost the same or more
there, so neither is used.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 5.0
KEY_PREFIX = "rl:"

# Multi-part public suffixes we actually meet. Without these, `example.co.in`
# reduces to `co.in` and every Indian company site shares one bucket.
_MULTI_PART_SUFFIXES = frozenset(
    {
        "co.in", "co.uk", "com.au", "co.jp", "co.nz", "com.br", "com.sg",
        "co.za", "com.mx", "co.kr", "com.tr", "org.uk", "net.in", "org.in",
        "ac.in", "gov.in", "com.cn", "co.il",
    }
)


def registrable_domain(url_or_host: str) -> str:
    """Reduce a URL or hostname to the domain we rate-limit on."""
    candidate = url_or_host.strip().lower()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or ""
    candidate = candidate.split("/")[0].split(":")[0].strip(".")
    if not candidate:
        return "unknown"

    parts = candidate.split(".")
    if len(parts) <= 2:
        return candidate
    if ".".join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class RateLimiter:
    """One request per `interval` per registrable domain."""

    def __init__(self, redis_client=None, interval: float = DEFAULT_INTERVAL_SECONDS):
        self.redis = redis_client
        self.interval = interval
        self._memory_locks: dict[str, float] = {}
        if redis_client is None:
            logger.warning(
                "REDIS_URL not set, using in-memory rate limiter "
                "(not safe for multi-worker)"
            )

    async def acquire(self, domain: str) -> bool:
        """True if the caller may proceed now. Non-blocking, 1 Redis command."""
        key = registrable_domain(domain)

        if self.redis is None:
            return self._acquire_memory(key)

        try:
            # NX makes this atomic: whoever sets the key wins, everyone else is
            # told to wait. EX expires it so no cleanup pass is needed.
            acquired = await self.redis.set(
                f"{KEY_PREFIX}{key}", "1", nx=True, ex=int(self.interval)
            )
        except Exception as exc:  # noqa: BLE001 — Redis down must not stop crawling
            logger.warning("rate limiter Redis error for %s (%s); allowing", key, exc)
            return True

        return bool(acquired)

    def _acquire_memory(self, key: str) -> bool:
        now = time.monotonic()
        last = self._memory_locks.get(key)
        if last is not None and now - last < self.interval:
            return False
        self._memory_locks[key] = now
        return True

    async def wait_and_acquire(self, domain: str, timeout: float = 30.0) -> bool:
        """Block until the domain frees up, or give up after `timeout`.

        Polls rather than subscribing: the wait is short by construction and a
        pubsub channel would cost far more Upstash commands than the polling.
        """
        deadline = time.monotonic() + timeout
        while True:
            if await self.acquire(domain):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info("rate limit wait timed out for %s", domain)
                return False
            await asyncio.sleep(min(1.0, remaining))


async def build_rate_limiter(redis_url: str | None) -> RateLimiter:
    """Construct a limiter, degrading to in-memory when Redis is unreachable."""
    if not redis_url:
        return RateLimiter(None)
    try:
        from redis.asyncio import from_url

        client = from_url(redis_url, socket_connect_timeout=5, decode_responses=True)
        await client.ping()
        return RateLimiter(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unreachable (%s); using in-memory rate limiter", exc)
        return RateLimiter(None)
