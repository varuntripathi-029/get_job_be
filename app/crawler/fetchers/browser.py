"""Headless Chromium, for pages that render their content in JavaScript.

The most expensive tier by an order of magnitude — a browser launch costs
seconds and hundreds of MB against milliseconds for an HTTP GET. It is reached
only when a source is explicitly flagged `requires_js`, and a semaphore caps how
many run at once so a batch of JS-heavy sources cannot exhaust a free-tier box.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from app.config import settings
from app.crawler.fetchers.base import (
    BaseFetcher,
    FetchResult,
    content_hash,
    failure,
)
from app.crawler.fetchers.html_text import html_to_text

logger = logging.getLogger(__name__)

# Module-level so the cap is shared by every fetcher instance in the process.
_semaphore = asyncio.Semaphore(settings.playwright_max_concurrent)


class PlaywrightFetcher(BaseFetcher):
    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return failure(
                "Playwright is not installed. Run: uv run playwright install chromium"
            )

        async with _semaphore:
            browser = None
            context = None
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    # A fresh context per fetch: no cookie or storage bleed
                    # between the sites we crawl.
                    context = await browser.new_context(
                        user_agent=settings.crawler_user_agent
                    )
                    page = await context.new_page()
                    timeout_ms = settings.playwright_timeout_seconds * 1000
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    html = await page.content()
            except Exception as exc:  # noqa: BLE001 — playwright raises many types
                elapsed = int((time.monotonic() - started) * 1000)
                logger.warning("playwright failed for %s: %s", url, exc)
                return failure(f"browser fetch failed: {exc}", duration_ms=elapsed)
            finally:
                # Closed in order, and guarded, because a launch that failed
                # halfway leaves one of these None.
                for closeable in (context, browser):
                    if closeable is not None:
                        with suppress(Exception):
                            await closeable.close()

        elapsed = int((time.monotonic() - started) * 1000)
        text = html_to_text(html)
        return FetchResult(
            content=text,
            content_hash=content_hash(text),
            http_status=200,
            content_type="text/html",
            duration_ms=elapsed,
        )
