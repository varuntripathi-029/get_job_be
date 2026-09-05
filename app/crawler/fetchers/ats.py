"""ATS job-board APIs — Greenhouse, Lever, Ashby, Keka.

The highest-value tier by a wide margin: structured JSON, no authentication, no
HTML parsing, no LLM. A company on a supported ATS gives an exact job list, so
`career_page_update` events here are counted rather than inferred.

Greenhouse, Lever and Ashby put the board token in the source URL, so one
`GET` reads the whole board. Keka is different: its careers page is a JS app
whose board id is a GUID that never appears in the URL, so its feed is a
two-step fetch — read the public portal info, pull the board id out of it, then
read the jobs. Both hops are anonymous, so it still costs no auth and no
browser. See `_fetch_keka`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
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

# Providers whose board token sits in the URL, so the API URL is a template.
# Keka is deliberately absent: its feed needs a runtime board-id lookup, so it
# has no static template and is handled by `_fetch_keka`.
_API_TEMPLATES = {
    "greenhouse": GREENHOUSE_API,
    "lever": LEVER_API,
    "ashby": ASHBY_API,
}

# Keka's careers SPA calls these, both anonymous. The portal-info response
# carries the board GUID inside its document paths; the active feed is the job
# list; jobdetails is the public per-role page used as the apply link.
KEKA_PORTAL_INFO = (
    "https://{tenant}.keka.com/careers/api/organization/default/careerportalinfo"
)
KEKA_ACTIVE_JOBS = (
    "https://{tenant}.keka.com/careers/api/embedjobs/default/active/{board_id}"
)
KEKA_JOB_DETAIL = "https://{tenant}.keka.com/careers/jobdetails/{job_id}"

# The board id has no field of its own; it is embedded in every document path
# the portal serves, as /ats/documents/<guid>/careerportal/... — so it is read
# out of the first such path found rather than a named key.
_KEKA_BOARD_ID = re.compile(r"/ats/documents/([0-9a-f-]{36})/", re.I)

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
    # The token here is the tenant subdomain, not the board id — the board id is
    # discovered at fetch time. `www` is filtered below like the others.
    ("keka", re.compile(r"//([a-z0-9-]+)\.keka\.com", re.I)),
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
    """Return `(provider, token, api_url)` for a template-based board URL.

    Returns None for Keka: its API URL cannot be built without a network
    lookup, so callers that need a static URL (the seed script) correctly treat
    it as not-a-simple-board. The crawler reaches Keka through `ATSFetcher`.
    """
    detected = detect_provider(url)
    if detected is None:
        return None
    provider, token = detected
    template = _API_TEMPLATES.get(provider)
    if template is None:
        return None
    return provider, token, template.format(token=token)


class ATSFetcher(BaseFetcher):
    """Fetches a board's full job list. No pagination needed: all three APIs
    return every open posting in one response."""

    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        detected = detect_provider(url)
        if detected is None:
            return failure(f"No supported ATS detected in {url!r}")

        provider, token = detected
        # Keka needs a two-step, board-id-discovering fetch of its own.
        if provider == "keka":
            return await _fetch_keka(token, started)

        api_url = _API_TEMPLATES[provider].format(token=token)
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


# --- Keka -------------------------------------------------------------------


async def _keka_get_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url, headers=default_headers())
    response.raise_for_status()
    return response.json()


def _keka_board_id(portal_info: dict[str, Any]) -> str | None:
    """Pull the board GUID out of the portal's document paths."""
    for value in portal_info.values():
        if isinstance(value, str):
            match = _KEKA_BOARD_ID.search(value)
            if match:
                return match.group(1)
    return None


async def _fetch_keka(tenant: str, started: float) -> FetchResult:
    """Discover the board id from portal info, then read the active job feed."""
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as client:
            info = await _keka_get_json(
                client, KEKA_PORTAL_INFO.format(tenant=tenant)
            )
            board_id = _keka_board_id(info) if isinstance(info, dict) else None
            if not board_id:
                elapsed = int((time.monotonic() - started) * 1000)
                return failure(
                    f"keka: no board id in portal info for {tenant!r}",
                    duration_ms=elapsed,
                )
            raw = await _keka_get_json(
                client, KEKA_ACTIVE_JOBS.format(tenant=tenant, board_id=board_id)
            )
    except httpx.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return failure(f"keka request failed: {exc}", duration_ms=elapsed)

    elapsed = int((time.monotonic() - started) * 1000)
    if not isinstance(raw, list):
        return failure(
            "keka active-jobs response was not a list", status=200, duration_ms=elapsed
        )

    jobs = [_keka(job, tenant) for job in raw if isinstance(job, dict)]
    payload = json.dumps(jobs, sort_keys=True, default=str)
    logger.info("keka board %s (%s) returned %d jobs", board_id, tenant, len(jobs))
    return FetchResult(
        content=payload,
        content_hash=content_hash(payload),
        http_status=200,
        content_type="application/json",
        duration_ms=elapsed,
    )


def _keka(job: dict[str, Any], tenant: str) -> dict[str, Any]:
    locations = job.get("jobLocations") or []
    location = None
    if locations and isinstance(locations[0], dict):
        first = locations[0]
        parts = [first.get("city"), first.get("state")]
        location = ", ".join(p for p in parts if p) or first.get("name")

    job_id = job.get("id")
    apply_url = (
        KEKA_JOB_DETAIL.format(tenant=tenant, job_id=job_id)
        if job_id is not None
        else None
    )
    return {
        "external_id": str(job_id),
        "title": (job.get("title") or "").strip(),
        "description_text": _clean(job.get("description")),
        "department": job.get("departmentName"),
        "location_raw": location,
        "application_url": apply_url,
        "published_at": job.get("publishedOn"),
    }


# --- company identity, for source-driven onboarding -------------------------


@dataclass(frozen=True)
class CompanyIdentity:
    """A company's name and domain, derived from an ATS source itself."""

    name: str
    domain: str


async def describe_company(url: str) -> CompanyIdentity | None:
    """Best-effort company identity for an ATS source, so it can onboard itself.

    Only returns an identity when the provider exposes a trustworthy name *and*
    domain. `canonical_domain` is the unique dedup key and the anchor for news
    entity-resolution, so a guessed domain would do lasting damage — better to
    return None and let an admin attach the company by hand.

    Keka's portal info carries both. Greenhouse/Lever/Ashby give a reliable
    name but no clean domain, so they return None for now.
    """
    detected = detect_provider(url)
    if detected is None:
        return None
    provider, token = detected
    if provider == "keka":
        return await _keka_identity(token)
    return None


async def _keka_identity(tenant: str) -> CompanyIdentity | None:
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as client:
            info = await _keka_get_json(
                client, KEKA_PORTAL_INFO.format(tenant=tenant)
            )
    except httpx.HTTPError as exc:
        logger.warning("keka identity lookup failed for %s: %s", tenant, exc)
        return None

    if not isinstance(info, dict):
        return None
    name = (info.get("name") or "").strip()
    if not name:
        return None
    # Prefer the real company domain; fall back to the tenant host, which is at
    # least unique per company so it never collides two Keka tenants.
    website = (info.get("companyWebsite") or "").strip()
    return CompanyIdentity(name=name, domain=website or f"{tenant}.keka.com")


def board_host(url: str) -> str:
    return urlparse(url).hostname or url
