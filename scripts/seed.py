"""Seed companies and sources from the markdown source registry.

Run: `uv run python -m scripts.seed [--validate]`

Idempotent: re-running skips anything already present, so it is safe against a
populated database. Parsing rules live in `scripts.registry`.

Most rows in the registry have no career page and no ATS — that is expected,
not a gap. A company with zero sources is still worth a row: news feeds, VC
posts and funding coverage are crawled unattached, and every article is
resolved back to a company by name. An early-stage company is visible through
what is written *about* it long before it publishes a job board, and that
expansion signal is the point of tracking it.

The ATS column is the one field worth distrusting outright — it names a vendor
but never a board token, and the token is only usually the company name. With
`--validate` each candidate board is probed and only a board that answers is
approved; the rest wait for a human instead of failing on every crawl.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

# Registers every model on Base.metadata. Importing only Company and Source
# leaves sources.submitted_by pointing at an unregistered users table, and
# SQLAlchemy raises NoReferencedTableError on the first flush.
import app.models  # noqa: F401
from app.companies.models import Company
from app.companies.service import normalize_domain, slugify
from app.config import settings
from app.crawler.fetchers.ats import api_url_for
from app.database import AsyncSessionLocal
from app.sources.models import Source
from scripts import registry
from scripts.registry import Row

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed")

REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "hiring_intelligence_source_registry.md"
)

# Domains that host many organisations' content. A board token derived from one
# identifies the platform, not the company.
SHARED_HOSTS = {
    "medium.com", "github.io", "substack.com", "wordpress.com", "blogspot.com",
    "netlify.app", "vercel.app", "notion.site", "wixsite.com", "ghost.io",
}

# (column, source_type, fetch_tier, floor minutes, default minutes, reliability)
#
# The floor caps how often the registry's requested cadence is honoured. A
# careers page marked "Daily" is worth a daily fetch; a GitHub org page marked
# "Daily" is not — the interesting part of an org is release velocity, which the
# page barely reflects, so it is held to weekly whatever the registry asks for.
COLUMN_SOURCES = (
    ("career_page", "career_page", "static_http", 720, 1440, 0.9),
    ("company_blog", "company_blog", "static_http", 720, 1440, 0.8),
    ("engineering_blog", "engineering_blog", "static_http", 1440, 2880, 0.8),
    ("rss_feed", "rss_feed", "rss", 180, 360, 0.7),
    ("github_organization", "github_org", "static_http", 10080, 10080, 0.5),
)
# For a publication, the company's own career page is not a signal about anyone
# we track — only what it publishes is.
NEWS_COLUMNS = {"company_blog", "engineering_blog", "rss_feed"}

ATS_FREQUENCY_MINUTES = 720

# Subdomains that front a company's site rather than identify it. Stripping
# them keeps one company on one canonical domain no matter which of its URLs a
# section happened to list: jobs.netflix.com and netflixtechblog.com should not
# become two Netflixes.
_SITE_PREFIXES = (
    "jobs.", "careers.", "career.", "hiring.", "hire.", "apply.",
    "blog.", "blogs.", "eng.", "engineering.", "tech.", "developer.",
    "developers.", "news.", "about.", "life.", "www.",
)


def _company_domain(row: Row) -> str:
    """The registrable-ish domain identifying the company a row describes.

    The Website column is not always the company's own site. The
    engineering-blog section lists the blog there, and several of those live on
    Medium — "Airbnb Engineering" and "Pinterest Engineering" both reduce to
    medium.com, which is a unique column, so the second row would silently be
    merged into the first company. So a shared host is rejected and the next
    column that names something company-specific is used instead.
    """
    for column in ("website", "career_page", "company_blog", "engineering_blog"):
        domain = normalize_domain(row.url(column))
        if not domain or domain in SHARED_HOSTS:
            continue
        for prefix in _SITE_PREFIXES:
            if domain.startswith(prefix) and domain.count(".") > 1:
                domain = domain[len(prefix):]
                break
        return domain
    return ""


@dataclass
class Stats:
    companies_inserted: int = 0
    companies_updated: int = 0
    companies_skipped: int = 0
    sources_by_type: Counter = field(default_factory=Counter)
    sources_skipped: int = 0
    rows_without_website: int = 0
    ats_validated: int = 0
    ats_failed: int = 0


# Board URL shapes for the vendors whose public APIs the fetcher speaks.
_BOARD_TEMPLATES = {
    "greenhouse": "https://boards.greenhouse.io/{token}",
    "lever": "https://jobs.lever.co/{token}",
    "ashby": "https://jobs.ashbyhq.com/{token}",
}


def _token_candidates(row: Row) -> list[str]:
    """Plausible board tokens for a company, most likely first.

    Boards are keyed by a vendor-side token that is usually — but not reliably —
    the company name lowercased. Postman's Greenhouse board is "postman";
    Razorpay's does not exist at "razorpay" despite the registry claiming
    Greenhouse. So candidates are guesses to be validated, never assumed.
    """
    name = row.name.lower()
    candidates = [slugify(row.name), "".join(c for c in name if c.isalnum())]
    # The registrable domain often matches the token where the name does not
    # (e.g. "cred.club" -> "cred") — but only when the domain belongs to the
    # company. Many engineering blogs live on shared platforms, and deriving a
    # token from those produces a board belonging to the platform: "Airbnb
    # Engineering" is hosted on Medium, and greenhouse.io/medium exists, so the
    # guess validates while attributing Medium's jobs to Airbnb.
    if row.website:
        host = normalize_domain(row.website)
        if host and host not in SHARED_HOSTS:
            candidates.append(host.split(".")[0])
    seen: set[str] = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _ats_board_url(row: Row) -> tuple[str | None, bool]:
    """A usable ATS board URL for a row, and whether it is a guess.

    First any URL column that already *is* a known board — that came from the
    registry and is a fact about the company. Failing that, a URL synthesised
    from the `ats_used` vendor: the registry names the vendor but never the
    board token, so without this path no ATS source is ever seeded, and that is
    the single most valuable tier. But the token is only usually the company
    name, so a synthesised board is flagged as a guess and never approved on
    trust — three of three guessed this way were dead on the current registry.
    """
    for column in ("career_page", "ats_board", "website", "api_documentation"):
        url = row.url(column)
        if url and api_url_for(url):
            return url, False

    template = _BOARD_TEMPLATES.get(row.data.get("ats_used", "").strip().lower())
    if template is None:
        return None, False
    candidates = _token_candidates(row)
    if not candidates:
        return None, False
    return template.format(token=candidates[0]), True


async def _probe(url: str) -> bool:
    """Whether an ATS board actually answers, so a wrong guess is not approved."""
    import httpx

    target = api_url_for(url)
    if target is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(
                target[2], headers={"User-Agent": settings.crawler_user_agent}
            )
        return response.status_code == 200
    except Exception:  # noqa: BLE001 — unreachable counts as unverified
        return False


async def _discover_ats_board(row: Row) -> str | None:
    """Probe every candidate token and return the first board that responds."""
    template = _BOARD_TEMPLATES.get(row.data.get("ats_used", "").strip().lower())
    if template is None:
        return None
    for token in _token_candidates(row):
        url = template.format(token=token)
        if await _probe(url):
            return url
    return None


def _frequency(row: Row, floor: int, default: int) -> int:
    requested = row.frequency_minutes
    return max(requested, floor) if requested is not None else default


class _Seeder:
    def __init__(self, db, now: datetime) -> None:
        self.db = db
        self.now = now
        self.stats = Stats()
        self.by_slug: dict[str, Company] = {}
        self.by_domain: dict[str, Company] = {}
        self.by_name: dict[str, Company] = {}
        self.urls: set[str] = set()

    async def load_existing(self) -> None:
        for company in (await self.db.execute(select(Company))).scalars():
            self.by_slug[company.slug] = company
            self.by_domain[company.canonical_domain] = company
            self.by_name[company.name.lower()] = company
        self.urls = set((await self.db.execute(select(Source.url))).scalars().all())

    def _remember(self, company: Company) -> None:
        self.by_slug[company.slug] = company
        self.by_domain[company.canonical_domain] = company
        self.by_name[company.name.lower()] = company

    async def company_for(self, row: Row) -> Company | None:
        """Find or create the company a row describes.

        Publications never create one. A VC or an accelerator has a website and
        a careers page of its own, but it is not an entity whose hiring this
        product reports on — seeding it would put Blume Ventures on the
        dashboard next to Razorpay with a momentum score. What is worth having
        is what it publishes, and that is attached with no company at all.

        Enrichment rows never create either: they name a parent that should
        already exist from a section carrying its website, and inventing one
        from a GitHub handle would produce a second row for a company we
        already track.
        """
        if row.is_news:
            return None

        if row.is_enrichment:
            return self.by_name.get(row.name.lower()) or self.by_slug.get(
                slugify(row.name)
            )

        domain = _company_domain(row)
        if not domain:
            self.stats.rows_without_website += 1
            logger.info("no usable domain for %r — skipping", row.name)
            return None

        slug = slugify(row.name)
        existing = self.by_domain.get(domain) or self.by_slug.get(slug)

        if existing is not None:
            self.stats.companies_skipped += 1
            if self._merge_aliases(existing, row):
                self.stats.companies_updated += 1
            return existing

        company = Company(
            slug=slug,
            name=row.name,
            canonical_domain=domain,
            website=row.website or f"https://{domain}",
            aliases=row.aliases or None,
            industry=row.data.get("category") or None,
            location_hq=row.data.get("country") or None,
            is_active=True,
        )
        self.db.add(company)
        await self.db.flush()
        self._remember(company)
        self.stats.companies_inserted += 1
        return company

    def _merge_aliases(self, company: Company, row: Row) -> bool:
        """Add names this row knows that the stored row does not.

        A company appears in several sections — Sarvam is in both
        `indian_unicorns` and `ai_companies` — and the sections spell it
        differently ("Sarvam" vs "Sarvam AI"). Every spelling is one more way a
        news article can resolve to it.
        """
        known = {company.name.lower(), *(a.lower() for a in company.aliases or [])}
        extra = [
            a for a in [row.name, *row.aliases] if a.lower() not in known
        ]
        if not extra:
            return False
        company.aliases = [*(company.aliases or []), *extra]
        return True

    def add_source(self, *, url: str, company: Company | None, **kwargs) -> bool:
        if not url or url in self.urls:
            self.stats.sources_skipped += int(bool(url))
            return False
        self.db.add(
            Source(
                company_id=company.id if company is not None else None,
                url=url,
                **kwargs,
            )
        )
        self.urls.add(url)
        self.stats.sources_by_type[kwargs["source_type"]] += 1
        return True

    async def seed_ats(self, row: Row, company: Company, *, validate: bool) -> None:
        board, is_guess = _ats_board_url(row)
        # Approved on one of two grounds: the registry named the board outright,
        # or a probe found it. A guessed token that nobody checked is neither.
        approved = not is_guess

        if validate:
            probed = await _discover_ats_board(row)
            if probed is not None:
                board, approved = probed, True
                self.stats.ats_validated += 1
            elif board is not None:
                approved = False
                self.stats.ats_failed += 1

        if not board:
            return

        detected = api_url_for(board)
        if detected and approved:
            company.ats_provider = detected[0]
            company.ats_board_url = board

        self.add_source(
            url=board,
            company=company,
            source_type="ats_api",
            fetch_tier="ats_api",
            # Unvalidated or failing boards wait for a human rather than
            # entering the crawl rotation and failing on every tick.
            status="approved" if approved else "pending",
            crawl_frequency_minutes=ATS_FREQUENCY_MINUTES,
            reliability_score=1.0,
            next_crawl_at=self.now if approved else None,
        )

    def seed_columns(self, row: Row, company: Company | None) -> None:
        for column, source_type, tier, floor, default, reliability in COLUMN_SOURCES:
            if row.is_news and column not in NEWS_COLUMNS:
                continue
            url = row.url(column)
            if not url:
                continue
            self.add_source(
                url=url,
                # NULL for publications: they cover many companies, and the
                # crawler resolves each entry to a company itself.
                company=None if row.is_news else company,
                source_type="news_site" if row.is_news else source_type,
                fetch_tier=tier,
                status="approved",
                crawl_frequency_minutes=_frequency(row, floor, default),
                reliability_score=row.reliability or reliability,
                requires_js=row.requires_js and tier == "static_http",
                next_crawl_at=self.now,
            )

    def seed_news_apis(self) -> None:
        """One global source per configured news API key."""
        configured = (
            (settings.newsapi_key, "newsapi://search", "news_api", "news_api", 360),
            (settings.gnews_api_key, "gnews://search", "news_api", "news_api", 360),
            (
                settings.serpapi_key,
                "serpapi://bing_news",
                "search_api",
                "search_api",
                1440,
            ),
        )
        for key, url, source_type, tier, frequency in configured:
            if not key:
                continue
            self.add_source(
                url=url,
                company=None,
                source_type=source_type,
                fetch_tier=tier,
                status="approved",
                crawl_frequency_minutes=frequency,
                reliability_score=0.7,
                next_crawl_at=self.now,
            )


async def seed(path: Path, *, validate: bool) -> Stats:
    rows = registry.parse(path)
    logger.info("parsed %d rows from %s", len(rows), path.name)

    async with AsyncSessionLocal() as db:
        seeder = _Seeder(db, datetime.now(UTC))
        await seeder.load_existing()

        for row in rows:
            company = await seeder.company_for(row)
            if company is None and not row.is_news:
                continue
            if company is not None and not row.is_enrichment:
                await seeder.seed_ats(row, company, validate=validate)
            seeder.seed_columns(row, company)

        seeder.seed_news_apis()
        await db.commit()

    return seeder.stats


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed companies and sources.")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Probe each ATS board; failures are seeded as 'pending' not "
        "'approved'. Slower, but the registry's ATS column is unreliable.",
    )
    args = parser.parse_args()

    if not args.registry.exists():
        logger.error("registry not found: %s", args.registry)
        return 1

    stats = await seed(args.registry, validate=args.validate)

    async with AsyncSessionLocal() as db:
        companies = await db.scalar(select(func.count()).select_from(Company))
        sources = await db.scalar(select(func.count()).select_from(Source))

    breakdown = ", ".join(
        f"{count} {name}" for name, count in sorted(stats.sources_by_type.items())
    )
    print()
    print("Seed complete:")
    print(
        f"  Companies: {stats.companies_inserted} inserted, "
        f"{stats.companies_skipped} already known "
        f"({stats.companies_updated} gained aliases)"
    )
    print(f"  Sources:   {sum(stats.sources_by_type.values())} inserted ({breakdown})")
    print(f"             {stats.sources_skipped} skipped as duplicates")
    if stats.ats_validated or stats.ats_failed:
        print(
            f"  ATS check: {stats.ats_validated} boards reachable, "
            f"{stats.ats_failed} unreachable (seeded as 'pending')"
        )
    if stats.rows_without_website:
        print(
            f"  Skipped:   {stats.rows_without_website} rows with no website "
            "(the registry flags these as unverified)"
        )
    print(f"  Totals now: {companies} companies, {sources} sources")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
