"""Job extraction from a company's own careers page.

The fallback for companies with no hosted ATS board — which, on any sample of
early-stage startups, is most of them. Strictly worse input than the ATS path:
no stable posting ids, no salary, no department, and a page that may render
differently on every crawl. Everything here exists to keep that unreliability
from corrupting the jobs table.

Two properties matter more than completeness:

- **Stable identity.** ATS boards hand out a posting id. A scraped page does
  not, so one is synthesised from the role's apply URL, falling back to
  title+location. If that key were unstable, every crawl would insert a
  duplicate of every role and orphan the previous set.
- **Never mass-close.** Callers pass these postings to `reconcile_jobs` with
  `allow_close=False`. A page that renders empty this morning is far more
  likely to be a slow render than forty roles closing overnight.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.config import settings
from app.extraction import llm
from app.extraction.prompts import careers_v1

logger = logging.getLogger(__name__)

# A careers page listing more than this is almost certainly a parse gone wrong
# — a nav menu read as roles, or the model repeating itself.
MAX_JOBS_PER_PAGE = 200

# Titles this short are navigation ("Jobs", "All"), not roles.
MIN_TITLE_CHARS = 3
MAX_TITLE_CHARS = 200


def synthetic_external_id(title: str, location: str | None, url: str | None) -> str:
    """A stable per-role key for a source that provides none.

    Prefers the role's own apply URL: it is the one field that identifies a
    posting across renders even when the title's whitespace or casing shifts.
    Title+location is the fallback, which is why a company posting the same
    role in two cities still yields two rows.
    """
    # Whitespace is collapsed here rather than relying on the caller: a title
    # that gains a stray double space between renders must not produce a new
    # id, or the same role is inserted twice.
    def _norm(value: str) -> str:
        return " ".join(value.split()).casefold()

    basis = url or f"{_norm(title)}|{_norm(location or '')}"
    digest = hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()
    # Prefixed so a scraped id is never mistaken for a vendor posting id.
    return f"cp_{digest[:20]}"


def _clean_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    if not (MIN_TITLE_CHARS <= len(title) <= MAX_TITLE_CHARS):
        return None
    return title


def normalise_career_jobs(
    raw: list[Any], page_url: str
) -> list[dict[str, Any]]:
    """Turn the model's output into postings `reconcile_jobs` understands."""
    postings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in raw[:MAX_JOBS_PER_PAGE]:
        if not isinstance(item, dict):
            continue
        title = _clean_title(item.get("title"))
        if title is None:
            continue

        location = item.get("location")
        location = " ".join(location.split()) if isinstance(location, str) else None

        url = item.get("url") if isinstance(item.get("url"), str) else None
        # Only keep a link that is actually a link; the model occasionally
        # echoes the role name into the url field.
        if url and not url.lower().startswith(("http://", "https://")):
            url = None

        external_id = synthetic_external_id(title, location, url)
        if external_id in seen:
            continue
        seen.add(external_id)

        postings.append(
            {
                "external_id": external_id,
                "title": title,
                "location_raw": location,
                # Falling back to the careers page is the point of the feature:
                # the user still lands somewhere they can apply.
                "application_url": url or page_url,
                "description_text": None,
                "department": None,
            }
        )

    return postings


async def extract_career_page_jobs(
    linked_text: str, page_url: str
) -> list[dict[str, Any]] | None:
    """Extract open roles from a careers page.

    Returns None when the model could not be reached or its response was
    unusable — distinct from `[]`, which means "the page genuinely lists no
    roles". Callers must not treat None as an empty board, or an LLM outage
    would read as every company closing every role at once.
    """
    result = await llm.complete_json(
        provider=settings.extractor_provider,
        model=settings.extractor_model,
        system_prompt=careers_v1.SYSTEM_PROMPT,
        user_content=linked_text,
        max_input_chars=careers_v1.MAX_INPUT_CHARS,
    )
    if result is None:
        logger.warning("careers extractor unavailable for %s", page_url)
        return None

    raw = result.data.get("jobs")
    if not isinstance(raw, list):
        logger.warning(
            "careers extractor returned no job list for %s: %s",
            page_url,
            list(result.data)[:5],
        )
        return None

    postings = normalise_career_jobs(raw, page_url)
    logger.info(
        "careers: %d roles from %s via %s in %sms",
        len(postings),
        page_url,
        result.model,
        result.latency_ms,
    )
    return postings
