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
product promises never to do. No match at all is an acceptable answer.
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
# Rebuilt at most this often. A crawl task lives well under this, so the index
# is built once per task and a company added mid-run is picked up on the next.
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class _Entry:
    """One indexed spelling: a company's name or one of its aliases."""

    company_id: object
    company_name: str
    spelling: str


@dataclass(frozen=True)
class Mention:
    company_id: object
    # The company. `matched` is the spelling that appeared, which may be an
    # alias — worth keeping apart when working out why something resolved.
    name: str
    matched: str
    count: int
    first_position: int

    @property
    def rank(self) -> tuple:
        """Sort key, best first.

        Whichever name is mentioned more often wins, since the subject of an
        article is repeated and the companies named in passing are not; then
        whichever is named earliest. Position decides "Zomato acquires Blinkit"
        in favour of the acquirer, which is the company the expansion signal
        belongs to.
        """
        return (
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
            self._entries[key] = _Entry(None, "", spelling)
            return
        self._entries[key] = _Entry(
            company_id=company.id,
            company_name=company.name,
            spelling=spelling,
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
                },
            )
            seen["count"] += 1

        return sorted((Mention(**s) for s in found.values()), key=lambda m: m.rank)


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
