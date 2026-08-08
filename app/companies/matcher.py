"""Find which tracked company a piece of prose is about.

`resolve_company` answers "is this string a company?" and is right to be strict:
it is fed a clean name or domain. This module answers the harder question the
crawler actually asks — "a headline mentions someone, who?" — where the name is
buried in a sentence.

That distinction matters more than it sounds. Most companies in the registry
publish no job board at all, so the only way they are ever observed is an
article written about them. Without prose matching those rows are inert.

The cost of a wrong answer is asymmetric and the rules here reflect it. A miss
loses one signal; a false match attaches someone else's funding round to a
company and shows it as evidence on their page, which is the one thing the
product promises never to do. So an ambiguous name has to clear a higher bar
than a distinctive one, and no match at all is an acceptable answer.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.models import Company

logger = logging.getLogger(__name__)

# Below this a name cannot be told from an initialism or a stray letter: "X"
# and "Fi" match constantly and mean nothing.
MIN_NAME_LENGTH = 3
# At or below this, a name is short enough to collide with ordinary text by
# chance, so it is held to the same rule as a name that is also a word. Ola,
# OYO and MPL are real companies and dropping them outright would be worse.
SHORT_NAME_LENGTH = 4

# Company names that are also ordinary English. Matching these case-insensitively
# anywhere in a sentence would tag every article that happens to use the word, so
# they are held to the stricter rule below. The list is deliberately literal —
# these are names actually present in the registry, not a general dictionary.
AMBIGUOUS_NAMES = frozenset(
    {
        "cred", "digit", "element", "emergent", "glance", "hike", "linear",
        "locus", "meta", "middleware", "notion", "open", "porter", "raise",
        "ramp", "slice", "snap", "turing", "zeta", "zone",
    }
)

# Words that make a sentence about a company rather than about a thing. An
# ambiguous name only counts as a match when one of these is nearby.
_CUE_RE = re.compile(
    r"\b(?:"
    r"raise[ds]?|raising|funding|funded|round|series\s+[a-e]|seed|valuation|"
    r"investors?|invests?|invested|backed|acquir\w+|merger|ipo|"
    r"startup|start-up|company|firm|platform|unicorn|founders?|"
    r"co-founder|ceo|cto|cfo|coo|hir(?:ing|es|ed)|headcount|employees|"
    r"engineers?|engineering|recruit\w*|onboard\w*|team|staff|"
    r"announced|launches|launched|expands?|expansion|appoints?|appointed|"
    # Leadership moves are a scored event type, and none of the words that
    # signal one appear above.
    r"exits?|departs?|departure|resign\w*|steps\s+down|joins?|joined|"
    r"promoted|elevated|named|leaves|leaving|"
    r"crore|lakh|million|billion"
    # A currency amount is as strong a cue as any word, but it cannot sit
    # inside the \b group: \b before "$" requires a word character before the
    # symbol, so "raised $5M" would never match while "US$5M" would.
    r")\b|[$₹]\s?\d",
    re.IGNORECASE,
)
# How far from the name a cue may sit and still be talking about it. One long
# sentence, roughly.
_CUE_WINDOW = 120

# Rebuilt at most this often. A crawl task lives well under this, so the index
# is built once per task and a company added mid-run is picked up on the next.
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class _Entry:
    """One indexed spelling: a company's name or one of its aliases."""

    company_id: object
    company_name: str
    spelling: str
    distinctive: bool


@dataclass(frozen=True)
class Mention:
    company_id: object
    # The company. `matched` is the spelling that appeared, which may be an
    # alias — worth keeping apart when working out why something resolved.
    name: str
    matched: str
    count: int
    first_position: int
    distinctive: bool

    @property
    def rank(self) -> tuple:
        """Sort key, best first.

        A distinctive name outranks an ambiguous one; then whichever is
        mentioned more often, since the subject of an article is repeated and
        the companies named in passing are not; then whichever is named
        earliest. Position decides "Zomato acquires Blinkit" in favour of the
        acquirer, which is the company the expansion signal belongs to.
        """
        return (
            not self.distinctive,
            -self.count,
            self.first_position,
            -len(self.matched),
        )


class CompanyIndex:
    """Compiled name -> company lookup over every tracked company."""

    def __init__(self, companies: list[Company]) -> None:
        self._entries: dict[str, _Entry] = {}
        for company in companies:
            for name in [company.name, *(company.aliases or [])]:
                self._add(name, company)
        # Longest first, so "Ola Electric" is tried before "Ola" and the
        # alternation does not settle for the shorter prefix.
        names = sorted(self._entries, key=len, reverse=True)
        self._pattern = (
            re.compile(
                r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")(?![\w])",
                re.IGNORECASE,
            )
            if names
            else None
        )

    def _add(self, name: str, company: Company) -> None:
        spelling = name.strip()
        if len(spelling) < MIN_NAME_LENGTH:
            return
        key = spelling.lower()
        existing = self._entries.get(key)
        # A name claimed by two companies identifies neither.
        if existing is not None and existing.company_id != company.id:
            self._entries[key] = _Entry(None, "", spelling, False)
            return
        self._entries[key] = _Entry(
            company_id=company.id,
            company_name=company.name,
            spelling=spelling,
            # Only single tokens are ever listed as ambiguous, so a multi-word
            # name is distinctive unless it is very short.
            distinctive=(
                key not in AMBIGUOUS_NAMES and len(key) > SHORT_NAME_LENGTH
            ),
        )

    def __len__(self) -> int:
        return len(self._entries)

    def find(self, text: str) -> list[Mention]:
        """Every tracked company mentioned in `text`, best match first."""
        if self._pattern is None or not text:
            return []

        found: dict[object, dict] = {}
        for match in self._pattern.finditer(text):
            entry = self._entries[match.group(1).lower()]
            if entry.company_id is None:
                continue
            if not entry.distinctive and not self._is_ambiguous_match(
                text, match, entry.spelling
            ):
                continue
            # Keyed by company, so a name and its alias in one article count as
            # one company mentioned twice rather than two companies.
            seen = found.setdefault(
                entry.company_id,
                {
                    "company_id": entry.company_id,
                    "name": entry.company_name,
                    "matched": match.group(1),
                    "count": 0,
                    "first_position": match.start(),
                    "distinctive": entry.distinctive,
                },
            )
            seen["count"] += 1

        return sorted((Mention(**s) for s in found.values()), key=lambda m: m.rank)

    @staticmethod
    def _is_ambiguous_match(text: str, match: re.Match[str], canonical: str) -> bool:
        """Whether a name that is also an ordinary word is being used as a name.

        Two things have to hold. The spelling must match the company's own
        casing, which rules out "open source" for Open and "linear regression"
        for Linear while still accepting "Open raised". And a corporate cue must
        sit nearby, because capitalisation alone is worth little at the start of
        a sentence.
        """
        if match.group(1) != canonical:
            return False
        window = text[
            max(0, match.start() - _CUE_WINDOW) : match.end() + _CUE_WINDOW
        ]
        return _CUE_RE.search(window) is not None


_cache: tuple[float, CompanyIndex] | None = None


async def get_index(db: AsyncSession, *, force: bool = False) -> CompanyIndex:
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    result = await db.execute(select(Company).where(Company.is_active.is_(True)))
    companies = list(result.scalars().all())
    index = CompanyIndex(companies)
    logger.info(
        "company index built: %d names over %d companies", len(index), len(companies)
    )
    _cache = (now, index)
    return index


def clear_cache() -> None:
    """Drop the cached index. Used by tests and after a seed run."""
    global _cache
    _cache = None


async def find_companies_in_text(db: AsyncSession, text: str) -> list[Mention]:
    index = await get_index(db)
    return index.find(text)


async def resolve_company_in_text(db: AsyncSession, text: str) -> Company | None:
    """The single company a piece of prose is most likely about, or None."""
    mentions = await find_companies_in_text(db, text)
    if not mentions:
        return None
    return await db.get(Company, mentions[0].company_id)
