"""Parser for the markdown source registry.

Kept separate from `seed.py` so the parsing rules can be tested without a
database. The registry is hand-maintained research output, so every rule here
assumes the file is imperfect and drops what it cannot verify rather than
seeding a plausible-looking guess.

Two registry formats exist and both parse:

- `hiring_intelligence_source_registry.md` — sections named `## indian_unicorns.txt`,
  abbreviated headers ("Career Pg"), and **scheme-less URLs** (`bytes.swiggy.com/feed`).
- `source-registry.md` — the older format, full headers and absolute URLs.

The differences are absorbed by header aliasing and URL normalisation, so
neither file needs editing to be seeded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NA_VALUES = {"", "n/a", "na", "none", "-", "unknown", "tbd"}

# Registry columns that mean the same thing under different spellings. Applied
# after slugifying the header cell, so "Career Pg" arrives here as "career_pg".
HEADER_ALIASES = {
    "career_pg": "career_page",
    "eng_blog": "engineering_blog",
    "github_org": "github_organization",
    "github_url": "github_organization",
    "public_api": "public_api_available",
    "api_docs": "api_documentation",
    "crawl_method": "crawl_method_recommendation",
    "frequency": "suggested_crawl_frequency",
    "crawl_frequency": "suggested_crawl_frequency",
    "feed_name": "name",
    "rss_atom_feed_url": "rss_feed",
    "source_category": "category",
    "parent_company": "name",
}

# How each section is treated. A section absent from all four sets is ignored
# with a warning, so a new section added to the registry is surfaced rather
# than silently seeded under the wrong rules.
COMPANY_SECTIONS = frozenset(
    {
        "indian_unicorns",
        "indian_startups",
        "ai_companies",
        "global_engineering_blogs",
    }
)
# Publications and funders. Their pages cover many companies, so the sources
# they yield carry company_id = NULL and the crawler resolves each entry to a
# company itself. A VC's "perspectives" page announcing a portfolio round is
# the same kind of signal as a news article about it.
NEWS_SECTIONS = frozenset(
    {"startup_news", "tech_news", "vcs", "accelerators", "rss_feeds"}
)
# Rows that describe a company already listed elsewhere. They may add a source
# but never create a company, because they carry no website to key one on.
ENRICHMENT_SECTIONS = frozenset({"github_orgs"})
# Documentation tables: endpoint patterns, coverage summaries, ToS notes.
IGNORED_SECTIONS = frozenset(
    {
        "ats_patterns",
        "job_aggregator_fallbacks",
        "summary_recommended_next_steps",
        "how_to_read_this_document",
        "sources",
    }
)

# A cell is only treated as a URL if it is *only* a URL. The registry annotates
# uncertain entries inline — "sebi.gov.in (no confirmed public RSS — verify
# before use)", "tech.flipkart.com (via Medium/GitHub eng blog network)" — and
# those must not be seeded as crawl targets.
_URL_RE = re.compile(
    r"^(?:https?://)?"           # scheme optional; the registry omits it
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"  # at least one dot
    r"(?:/[^\s]*)?$",
    re.IGNORECASE,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# "Zomato (Eternal)", "API Holdings (PharmEasy)", "Yubi (CredAvenue)" — the
# parenthetical is a real second name the press uses interchangeably.
_PAREN_RE = re.compile(r"\(([^)]+)\)")
# Qualifiers appended by the registry to disambiguate a row, not part of the
# company's name: "Zscaler India R&D", "NVIDIA (AI infra)", "Turing (India ops)".
_QUALIFIER_RE = re.compile(
    r"\b(india|global|us|uk)?\s*"
    r"(r&d|ops|operations|subsidiary|india ops|ai infra|infra|"
    r"posting surface, not ats|via rss|recruiter tool|vc arm)\b",
    re.IGNORECASE,
)
# The engineering-blog section names rows after the publication, not the
# company: "Netflix TechBlog", "Stripe Blog (Engineering)", "Amazon Science".
# The entity whose hiring we track is Netflix, and an article saying "Netflix"
# has to resolve to it, so the publication suffix is stripped.
_PUBLICATION_SUFFIX_RE = re.compile(
    r"\s*\b("
    r"tech ?blog|technology blog|engineering blog|eng blog|research blog|"
    r"machine learning research|code as craft|architecture blog|"
    r"engineering|blog|science|hacks|newsletter"
    r")\b\s*$",
    re.IGNORECASE,
)
# Words that are descriptive rather than identifying. As an alias any of these
# would match nearly every article, attaching other companies' funding rounds
# to whichever row happened to carry it.
_GENERIC_NAMES = frozenset(
    {
        "ai", "api", "blog", "eng", "engineering", "etc", "global", "group",
        "holdings", "inc", "india", "infra", "labs", "ltd", "newsletter",
        "news", "ops", "research", "science", "startup", "startups",
        "subsidiary", "subsidiary lines", "tech", "technology", "uk", "us",
        "usa", "ventures",
    }
)

# Registry cadence -> minutes. The registry asks for "Hourly" on high-volume
# feeds; the per-tier floors in seed.py keep that from overrunning the crawl
# budget on tiers where an hour of change is unlikely.
FREQUENCY_MINUTES = {
    "hourly": 60,
    "daily": 1440,
    "weekly": 10080,
    "biweekly": 20160,
    "monthly": 43200,
}

PRIORITY_RELIABILITY = {"high": 0.9, "medium": 0.7, "low": 0.5}


def slugify_header(header: str) -> str:
    return _SLUG_RE.sub("_", header.strip().lower()).strip("_")


def clean(cell: str) -> str:
    """Strip markdown emphasis and fold the registry's many spellings of null."""
    value = cell.strip().strip("*`").strip()
    return "" if value.lower() in NA_VALUES else value


def normalize_url(cell: str) -> str:
    """Return an absolute URL, or "" if the cell is not purely a URL.

    The newer registry writes bare hosts (`inc42.com/feed`). Prefixing https is
    safe: every entry is a public web resource, and a host that only speaks
    http fails the fetch rather than being crawled insecurely.
    """
    value = clean(cell)
    if not value or not _URL_RE.match(value):
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _tidy(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -–,")


@dataclass(frozen=True)
class Row:
    section: str
    data: dict[str, str]
    lineno: int

    @property
    def name(self) -> str:
        return _tidy(self.data.get("name", ""))

    @property
    def website(self) -> str:
        return normalize_url(self.data.get("website", ""))

    @property
    def is_company(self) -> bool:
        return self.section in COMPANY_SECTIONS

    @property
    def is_news(self) -> bool:
        return self.section in NEWS_SECTIONS

    @property
    def is_enrichment(self) -> bool:
        return self.section in ENRICHMENT_SECTIONS

    @property
    def requires_js(self) -> bool:
        return "playwright" in self.data.get(
            "crawl_method_recommendation", ""
        ).lower()

    @property
    def frequency_minutes(self) -> int | None:
        word = self.data.get("suggested_crawl_frequency", "").strip().lower()
        return FREQUENCY_MINUTES.get(word)

    @property
    def reliability(self) -> float | None:
        return PRIORITY_RELIABILITY.get(self.data.get("priority", "").strip().lower())

    def url(self, column: str) -> str:
        return normalize_url(self.data.get(column, ""))


def _map_headers(cells: list[str]) -> list[str]:
    """Slugify and alias header cells, keeping every column addressable.

    Two columns can alias onto one name — `github_orgs` has both "GitHub Org"
    (a bare handle) and "GitHub URL". The later column wins the canonical name
    and the earlier keeps its literal one, which is the right way round: the
    rightmost of a colliding pair is the more specific spelling in both tables
    where this happens.
    """
    mapped: list[str] = []
    for cell in cells:
        raw = slugify_header(cell)
        alias = HEADER_ALIASES.get(raw, raw)
        if alias in mapped:
            mapped[mapped.index(alias)] = f"_{alias}"
        mapped.append(alias)
    return mapped


def parse(path: Path) -> list[Row]:
    """Extract every table row, tagged with the `##` section it sits under.

    Anything malformed is skipped with a warning rather than aborting: a
    hand-edited registry should not be able to block a seed run outright.
    """
    rows: list[Row] = []
    section = "unknown"
    headers: list[str] = []
    unknown_sections: set[str] = set()

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()

        if line.startswith("#"):
            # Headings carry trailing editorial notes in italics —
            # "## job_aggregator_fallbacks.txt *(new — added per follow-up
            # request)*" — which are not part of the section's identity.
            heading = re.sub(r"\*.*$", "", line.lstrip("#")).strip()
            # Sections are named after the virtual file they'd be exported to.
            section = slugify_header(heading).removesuffix("_txt")
            headers = []
            continue

        if not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]

        # A separator row (|---|---|) closes the header.
        if all(c and set(c) <= {"-", ":", " "} for c in cells):
            continue

        if not headers:
            headers = _map_headers(cells)
            continue

        if len(cells) != len(headers):
            logger.warning(
                "%s:%d — %d cells, expected %d; skipping",
                path.name, lineno, len(cells), len(headers),
            )
            continue

        known = (
            COMPANY_SECTIONS | NEWS_SECTIONS | ENRICHMENT_SECTIONS | IGNORED_SECTIONS
        )
        if section not in known:
            unknown_sections.add(section)
            continue
        if section in IGNORED_SECTIONS:
            continue

        data = {h: clean(c) for h, c in zip(headers, cells, strict=True)}
        if data.get("name"):
            rows.append(Row(section=section, data=data, lineno=lineno))

    for name in sorted(unknown_sections):
        logger.warning(
            "unhandled registry section %r — add it to one of the *_SECTIONS "
            "sets in scripts/registry.py to seed it",
            name,
        )

    return rows
