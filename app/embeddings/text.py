"""Canonical text construction and hashing for embeddings.

Every embedding input is built here. Inline string concatenation at call sites
is what lets a job get embedded one way by the crawler and a different way by
the backfill worker, producing vectors that cannot be compared — the bug this
module exists to prevent.

The hash is taken over the normalised text, so trailing whitespace or a change
of case never triggers a paid re-embed.
"""

from __future__ import annotations

import hashlib
import re

# Job descriptions end in identical benefits and equal-opportunity boilerplate.
# Past this point the text stops distinguishing one role from another.
MAX_DESCRIPTION_CHARS = 500

# Well inside every provider's input limit; longer text adds cost, not signal.
MAX_INPUT_CHARS = 8000

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace.

    Used for hashing only — the text actually sent to the provider keeps its
    original casing, which carries meaning for named entities.
    """
    return _WHITESPACE.sub(" ", (text or "").strip().lower())


def content_hash(text: str) -> str:
    """Stable SHA-256 over the normalised text."""
    return hashlib.sha256(normalize(text).encode("utf-8", "replace")).hexdigest()


def truncate(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())[:MAX_INPUT_CHARS]


def build_job_embedding_text(
    title: str,
    department: str | None = None,
    role_family: str | None = None,
    description: str | None = None,
) -> str:
    """The text a job is embedded from."""
    parts = [
        title or "",
        department or "",
        role_family or "",
        (description or "")[:MAX_DESCRIPTION_CHARS],
    ]
    return truncate(" ".join(part for part in parts if part.strip()))


def build_resume_embedding_text(
    skills: list[str] | None = None,
    role_families: list[str] | None = None,
    seniority: str | None = None,
    locations: list[str] | None = None,
) -> str:
    """The text a resume is embedded from.

    Deliberately the parsed fields rather than the raw resume: raw text is
    dominated by employer names, dates and formatting boilerplate, which pull
    the vector away from what the candidate can actually do.
    """
    parts = [
        " ".join(skills or []),
        " ".join(role_families or []),
        seniority or "",
        " ".join(locations or []),
    ]
    return truncate(" ".join(part for part in parts if part.strip()))
