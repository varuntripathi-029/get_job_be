"""HTML to readable text, shared by the static and Playwright fetchers."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Tags whose text is never content, only chrome.
_STRIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "svg")


def html_to_text(html: str) -> str:
    """Extract the readable body of a page.

    readability-lxml first — it isolates the article and discards navigation,
    which matters because a company blog's sidebar mentions "careers" on every
    page and would otherwise trip the pre-filter on every crawl. It fails on
    pages that are not article-shaped, so BeautifulSoup on the raw HTML is the
    fallback.
    """
    extracted = ""
    try:
        from readability import Document

        summary = Document(html).summary()
        extracted = _soup_text(summary)
    except Exception as exc:  # noqa: BLE001 — readability is best-effort
        logger.debug("readability failed, falling back to raw parse: %s", exc)

    # Readability sometimes returns a near-empty shell for listing pages.
    if len(extracted) < 200:
        full = _soup_text(html)
        if len(full) > len(extracted):
            extracted = full

    return extracted


def _soup_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Collapse the blank-line runs that stripping tags leaves behind.
    lines = [line for line in (ln.strip() for ln in text.split("\n")) if line]
    return "\n".join(lines)
