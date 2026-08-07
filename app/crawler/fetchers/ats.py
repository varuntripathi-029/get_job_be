"""ATS job-board APIs — Greenhouse, Lever, Ashby.

The highest-value tier by a wide margin: structured JSON, no authentication, no
HTML parsing, no LLM. A company on a supported ATS gives an exact job list, so
`career_page_update` events here are counted rather than inferred.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from app.crawler.fetchers.base import (
    DEFAULT_TIMEOUT,
    BaseFetcher,
    FetchResult,
    content_hash,
    default_headers,
    failure,
)
from app.crawler.fetchers.html_text import html_to_text

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_API = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{token}"

# (provider, regex over the full URL) -> board token from group 1.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "greenhouse",
        re.compile(
            r"boards(?:-api)?\.greenhouse\.io/"
            r"(?:embed/job_board\?for=)?([a-z0-9_-]+)",
            re.I,
        ),
    ),
    ("greenhouse", re.compile(r"job-boards\.greenhouse\.io/([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"//([a-z0-9-]+)\.greenhouse\.io", re.I)),
    (
        "lever",
        re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)", re.I),
    ),
    (
        "ashby",
        re.compile(
            r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([a-z0-9_-]+)",
            re.I,
        ),
    ),
)


def detect_provider(url: str) -> tuple[str, str] | None:
    """Return `(provider, board_token)` for a supported ATS URL, else None."""
    for provider, pattern in _PATTERNS:
        match = pattern.search(url)
        if match:
            token = match.group(1)
            # `boards.greenhouse.io/embed` and similar are paths, not tokens.
            if token.lower() in ("embed", "www", "api", "v0", "posting-api"):
                continue
            return provider, token
    return None


def api_url_for(url: str) -> tuple[str, str, str] | None:
    """Return `(provider, token, api_url)` for a board URL."""
    detected = detect_provider(url)
    if detected is None:
        return None
    provider, token = detected
    template = {
        "greenhouse": GREENHOUSE_API,
        "lever": LEVER_API,
        "ashby": ASHBY_API,
    }[provider]
    return provider, token, template.format(token=token)


class ATSFetcher(BaseFetcher):
    """Fetches a board's full job list. No pagination needed: all three APIs
    return every open posting in one response."""

    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        target = api_url_for(url)
        if target is None:
            return failure(f"No supported ATS detected in {url!r}")

        provider, _token, api_url = target
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(api_url, headers=default_headers())
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return failure(f"{provider} request failed: {exc}", duration_ms=elapsed)

        elapsed = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            return failure(
                f"{provider} returned {response.status_code}",
                status=response.status_code,
                duration_ms=elapsed,
            )

        try:
            jobs = _normalise(provider, response.json())
        except (ValueError, KeyError, TypeError) as exc:
            return failure(
                f"{provider} response was not the expected shape: {exc}",
                status=response.status_code,
                duration_ms=elapsed,
            )

        payload = json.dumps(jobs, sort_keys=True, default=str)
        logger.info("%s board %s returned %d jobs", provider, api_url, len(jobs))
        return FetchResult(
            content=payload,
            content_hash=content_hash(payload),
            http_status=response.status_code,
            content_type="application/json",
            duration_ms=elapsed,
        )

    async def fetch_jobs(self, url: str) -> list[dict[str, Any]]:
        """Normalised job dicts, or an empty list if the board could not be read."""
        result = await self.fetch(url)
        if not result.ok:
            logger.warning("ATS fetch failed for %s: %s", url, result.error)
            return []
        return json.loads(result.content)


def _clean(raw: str | None) -> str:
    """ATS descriptions are HTML fragments; store them as readable text.

    Entities are unescaped first because Greenhouse double-encodes: its
    `content` field arrives as `&lt;div class=&quot;...` rather than real
    markup, so a naive `"<" in raw` check misses it and the escaped source
    would be stored verbatim as the job description.
    """
    if not raw:
        return ""
    text = unescape(raw)
    return html_to_text(text) if "<" in text else text.strip()


def _normalise(provider: str, payload: Any) -> list[dict[str, Any]]:
    if provider == "greenhouse":
        return [_greenhouse(job) for job in payload.get("jobs", [])]
    if provider == "lever":
        # Lever returns a bare array.
        return [_lever(job) for job in payload]
    if provider == "ashby":
        return [_ashby(job) for job in payload.get("jobs", [])]
    raise ValueError(f"unknown provider {provider!r}")


def _greenhouse(job: dict[str, Any]) -> dict[str, Any]:
    departments = [d.get("name") for d in job.get("departments") or [] if d.get("name")]
    offices = [o.get("name") for o in job.get("offices") or [] if o.get("name")]
    location = (job.get("location") or {}).get("name") or (
        ", ".join(offices) if offices else None
    )
    return {
        "external_id": str(job.get("id")),
        "title": (job.get("title") or "").strip(),
        "description_text": _clean(job.get("content")),
        "department": departments[0] if departments else None,
        "location_raw": location,
        "application_url": job.get("absolute_url"),
        "published_at": job.get("updated_at") or job.get("first_published"),
    }


def _lever(job: dict[str, Any]) -> dict[str, Any]:
    categories = job.get("categories") or {}
    return {
        "external_id": str(job.get("id")),
        "title": (job.get("text") or "").strip(),
        "description_text": _clean(
            job.get("descriptionPlain") or job.get("description")
        ),
        "department": categories.get("department") or categories.get("team"),
        "location_raw": categories.get("location"),
        "application_url": job.get("hostedUrl") or job.get("applyUrl"),
        "published_at": job.get("createdAt"),
    }


def _ashby(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": str(job.get("id")),
        "title": (job.get("title") or "").strip(),
        "description_text": _clean(
            job.get("descriptionPlain") or job.get("descriptionHtml")
        ),
        "department": job.get("department") or job.get("team"),
        "location_raw": job.get("location"),
        "application_url": job.get("jobUrl") or job.get("applyUrl"),
        "published_at": job.get("publishedAt"),
    }


def board_host(url: str) -> str:
    return urlparse(url).hostname or url
