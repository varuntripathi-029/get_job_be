"""RSS/Atom feeds.

Cheaper and more reliable than scraping the same blog as HTML: entries arrive
dated and separated, so the extractor sees one post at a time instead of a page
containing five.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import feedparser
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

MAX_ENTRIES = 25


def _published(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _body(entry) -> str:
    if entry.get("content"):
        raw = entry["content"][0].get("value", "")
    else:
        raw = entry.get("summary") or entry.get("description") or ""
    return html_to_text(raw) if "<" in raw else raw.strip()


class RSSFetcher(BaseFetcher):
    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(url, headers=default_headers())
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return failure(f"feed request failed: {exc}", duration_ms=elapsed)

        elapsed = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            return failure(
                f"feed returned {response.status_code}",
                status=response.status_code,
                duration_ms=elapsed,
            )

        parsed = feedparser.parse(response.text)
        # bozo means malformed XML. Many real feeds are slightly malformed and
        # still parse, so this only matters when nothing came out.
        if parsed.bozo and not parsed.entries:
            return failure(
                f"feed did not parse: {parsed.get('bozo_exception')}",
                status=response.status_code,
                duration_ms=elapsed,
            )

        entries = [
            {
                "title": (entry.get("title") or "").strip(),
                "link": entry.get("link"),
                "published_at": _published(entry),
                "body": _body(entry),
            }
            for entry in parsed.entries[:MAX_ENTRIES]
        ]

        payload = json.dumps(entries, sort_keys=True)
        logger.info("feed %s produced %d entries", url, len(entries))
        return FetchResult(
            content=payload,
            # Hashed over the parsed entries, not the raw XML: many feeds embed
            # a build timestamp or a rotating <lastBuildDate>, which would make
            # every single crawl look like new content.
            content_hash=content_hash(payload),
            http_status=response.status_code,
            content_type="application/json",
            duration_ms=elapsed,
        )
