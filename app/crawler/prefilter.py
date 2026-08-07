"""Rule-based relevance gate, run before any LLM call.

Asymmetric on purpose. A false positive costs one classifier call at roughly
$0.0002; a false negative loses a signal permanently. So the default when
nothing matches is to pass the content through and let the model decide.

This is the cheapest stage in the pipeline and rejects the bulk of what crawling
turns up — festival posts, cookie banners, marketing pages.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MIN_LENGTH = 100
# Above this ratio of bracket/markup characters the "page" is a script dump or
# a JSON blob, not prose an extractor can read.
MAX_MARKUP_RATIO = 0.3
_MARKUP_CHARS = set("<>/{}[]")

# These source types are relevant by definition — a career page is about hiring
# whatever words happen to be on it.
ALWAYS_RELEVANT_SOURCE_TYPES = frozenset({"career_page", "ats_api"})

EXCLUDE_PATTERNS = (
    # Festival and holiday greetings. Extremely common on Indian company blogs
    # and never a hiring signal.
    "happy diwali", "merry christmas", "season's greetings", "happy holi",
    "eid mubarak", "happy new year", "happy independence day", "happy pongal",
    # Culture and employer-branding filler.
    "employee of the month", "meet our team", "day in the life", "team outing",
    "company picnic", "annual day", "women's day celebration",
    # Marketing.
    "limited time offer", "discount code", "sale ends", "coupon", "promo code",
    "subscribe to our channel", "use code",
    # Legal boilerplate, usually a consent banner captured as page text.
    "cookie policy", "privacy policy", "terms of service", "terms and conditions",
)

INCLUDE_PATTERNS = (
    "hiring", "we're hiring", "we are hiring", "join us", "open position",
    "job opening", "now hiring", "career opportunit",
    "funding", "raised", "series a", "series b", "series c", "series d",
    "seed round", "valuation", "investment",
    "office", "expansion", "expanding", "new facility",
    "launch", "acquired", "acquisition", "merger", "partnership",
    "engineer", "team growth", "headcount", "grew the team",
    "vp of", "cto", "ceo", "cfo", "leadership", "appointed", "joins as",
    "infrastructure", "data center", "datacenter", "cloud migration",
    "layoff", "restructur", "downsiz",
)

_EXCLUDE_RE = re.compile("|".join(re.escape(p) for p in EXCLUDE_PATTERNS), re.I)
_INCLUDE_RE = re.compile("|".join(re.escape(p) for p in INCLUDE_PATTERNS), re.I)


def markup_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if ch in _MARKUP_CHARS) / len(text)


def is_relevant(text: str, source_type: str) -> bool:
    """Whether content is worth spending a classifier call on."""
    if source_type in ALWAYS_RELEVANT_SOURCE_TYPES:
        return True

    stripped = (text or "").strip()

    if len(stripped) < MIN_LENGTH:
        logger.debug("prefilter: rejected, %d chars", len(stripped))
        return False

    if markup_ratio(stripped) > MAX_MARKUP_RATIO:
        logger.debug("prefilter: rejected, markup-heavy")
        return False

    # An include match beats an exclude match: a post that opens with a Diwali
    # greeting and goes on to announce a funding round is still a signal.
    if _INCLUDE_RE.search(stripped):
        return True

    if _EXCLUDE_RE.search(stripped):
        logger.debug("prefilter: rejected, matched an exclude pattern")
        return False

    # Nothing matched either way. Pass it on — losing a signal costs more than
    # one classifier call.
    return True
