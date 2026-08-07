"""Plain HTTP pages, reduced to readable text."""

from __future__ import annotations

import logging
import time

import httpx

from app.common.exceptions import SSRFError
from app.crawler.fetchers.base import (
    DEFAULT_TIMEOUT,
    BaseFetcher,
    FetchResult,
    content_hash,
    default_headers,
    failure,
)
from app.crawler.fetchers.html_text import html_to_text
from app.crawler.ssrf import MAX_REDIRECTS, validate_url

logger = logging.getLogger(__name__)


class StaticFetcher(BaseFetcher):
    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
            ) as client:
                response = await client.get(url, headers=default_headers())
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return failure(f"request failed: {exc}", duration_ms=elapsed)

        elapsed = int((time.monotonic() - started) * 1000)

        # Re-validate wherever we actually landed. A public URL is free to
        # redirect somewhere internal, and the check at submission time cannot
        # see that — this is the whole reason SSRF is validated twice.
        final_url = str(response.url)
        if final_url != url:
            try:
                validate_url(final_url)
            except SSRFError as exc:
                logger.warning("blocked redirect %s -> %s: %s", url, final_url, exc)
                return failure(
                    f"redirect target blocked: {exc}", duration_ms=elapsed
                )

        if response.status_code >= 400:
            return failure(
                f"HTTP {response.status_code}",
                status=response.status_code,
                duration_ms=elapsed,
            )

        text = html_to_text(response.text)
        return FetchResult(
            content=text,
            # Over the cleaned text, so rotating ads, CSRF tokens and build
            # hashes in the markup do not register as a content change.
            content_hash=content_hash(text),
            http_status=response.status_code,
            content_type=response.headers.get("content-type", ""),
            duration_ms=elapsed,
        )
