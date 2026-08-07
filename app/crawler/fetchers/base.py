"""Fetcher interface shared by every tier."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import settings

DEFAULT_TIMEOUT = 30.0


@dataclass(slots=True)
class FetchResult:
    """What every fetcher returns, whatever the tier.

    `content` is already normalised for its tier: readable text for HTML pages,
    a JSON string for feeds and APIs. `content_hash` is taken over that
    normalised form, not the raw bytes, so cosmetic markup churn — rotating ad
    slots, CSRF tokens, build hashes — does not read as a content change.
    """

    content: str
    content_hash: str
    http_status: int
    content_type: str
    duration_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.http_status < 300


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def default_headers() -> dict[str, str]:
    """Identify the crawler honestly, per robots convention."""
    return {
        "User-Agent": settings.crawler_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


class BaseFetcher(ABC):
    @abstractmethod
    async def fetch(self, url: str) -> FetchResult:
        """Retrieve a URL and return normalised content."""


def failure(error: str, *, status: int = 0, duration_ms: int = 0) -> FetchResult:
    """A FetchResult representing a failed attempt."""
    return FetchResult(
        content="",
        content_hash="",
        http_status=status,
        content_type="",
        duration_ms=duration_ms,
        error=error,
    )
