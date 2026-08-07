"""Seed companies and sources from source-registry.md.

Run: `uv run python -m scripts.seed`

Idempotent: re-running skips anything already present, so it is safe against a
populated database.

The registry is unverified research output — its own header says so, and the
ATS column is demonstrably wrong in places (it lists Greenhouse for companies
whose Greenhouse board 404s). Rather than trust it, `--validate` probes each ATS
board and marks the ones that fail as `pending` for review instead of
`approved`. Everything else is seeded optimistically; the crawler's failure
backoff disables what turns out to be dead.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed")

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "source-registry.md"

# Sections that describe integrations or funders, not companies whose hiring we
# track. Their rows still yield useful news/blog sources, but no Company row.
NON_COMPANY_SECTIONS = {"ats_patterns", "vcs", "accelerators"}
# Sections whose rows are publications: they cover many companies, so their
# sources carry company_id = NULL.
NEWS_SECTIONS = {"startup_news", "tech_news"}

NA_VALUES = {"", "n/a", "na", "none", "-", "unknown", "tbd"}

# Domains that host many organisations' content. A token derived from one
# identifies the platform, not the company.
SHARED_HOSTS = {
    "medium.com", "github.io", "substack.com", "wordpress.com", "blogspot.com",
    "netlify.app", "vercel.app", "notion.site", "wixsite.com", "ghost.io",
}

# (column, source_type, fetch_tier, frequency minutes, reliability)
COLUMN_SOURCES = (
    ("career_page", "career_page", "static_http", 1440, 0.9),
    ("company_blog", "company_blog", "static_http", 1440, 0.8),
    ("engineering_blog", "engineering_blog", "static_http", 2880, 0.8),
    ("rss_feed", "rss_feed", "rss", 360, 0.7),
    ("github_organization", "github_org", "static_http", 4320, 0.5),
)


def _clean(cell: str) -> str:
    value = cell.strip().strip("*` ")
    return "" if value.lower() in NA_VALUES else value


def _slugify_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


@dataclass
class Row:
    section: str
    data: dict[str, str]

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def website(self) -> str:
        return self.data.get("website", "")


@dataclass
class Stats:
    companies_inserted: int = 0
    companies_skipped: int = 0
    sources_by_type: Counter = field(default_factory=Counter)
    sources_skipped: int = 0
    rows_skipped: int = 0
    ats_validated: int = 0
    ats_failed: int = 0


def parse_registry(path: Path) -> list[Row]:
    """Extract every table row, tagged with the `##` section it sits under.

    The file is uniform: each section is one markdown table with identical
    columns. Anything that is not a well-formed row is skipped with a warning
    rather than aborting the seed.
    """
    rows: list[Row] = []
    section = "unknown"
    headers: list[str] = []

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()

        if line.startswith("## "):
            section = _slugify_header(line[3:])
            headers = []
            continue

        if not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]

        # A separator row (|---|---|) marks the end of the header.
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue

        if not headers:
            headers = [_slugify_header(c) for c in cells]
            continue

        if len(cells) != len(headers):
            logger.warning(
                "line %d: %d cells, expected %d", lineno, len(cells), len(headers)
            )
            continue

        data = {h: _clean(c) for h, c in zip(headers, cells, strict=True)}
        if data.get("name"):
            rows.append(Row(section=section, data=data))

    return rows


async def _validate_ats(url: str) -> bool:
    """Probe an ATS board so a wrong guess is not seeded as approved."""
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


async def seed(path: Path, *, validate: bool) -> Stats:
    stats = Stats()
    rows = parse_registry(path)
    logger.info("parsed %d rows from %s", len(rows), path.name)

    async with AsyncSessionLocal() as db:
        existing_slugs = set(
            (await db.execute(select(Company.slug))).scalars().all()
        )
        existing_domains = set(
            (await db.execute(select(Company.canonical_domain))).scalars().all()
        )
        existing_urls = set((await db.execute(select(Source.url))).scalars().all())
        now = datetime.now(UTC)

        for row in rows:
            is_news = row.section in NEWS_SECTIONS
            is_company = row.section not in NON_COMPANY_SECTIONS and not is_news

            company: Company | None = None

            if is_company:
                if not row.website:
                    stats.rows_skipped += 1
                    logger.warning("skipping %r: no website", row.name)
                    continue

                domain = normalize_domain(row.website)
                slug = slugify(row.name)

                if slug in existing_slugs or domain in existing_domains:
                    stats.companies_skipped += 1
                    company = await db.scalar(
                        select(Company).where(Company.canonical_domain == domain)
                    )
                else:
                    company = Company(
                        slug=slug,
                        name=row.name,
                        canonical_domain=domain,
                        website=row.website,
                        industry=row.data.get("category") or None,
                        location_hq=row.data.get("country") or None,
                        is_active=True,
                    )
                    db.add(company)
                    await db.flush()
                    existing_slugs.add(slug)
                    existing_domains.add(domain)
                    stats.companies_inserted += 1

            # --- ATS board -------------------------------------------------
            if company is not None:
                if validate:
                    # Probe every candidate token; a vendor named in the
                    # registry with no reachable board yields nothing rather
                    # than a source that 404s on every crawl.
                    board = await _discover_ats_board(row)
                    approved = board is not None
                    if board is None:
                        board = _ats_board_url(row)
                        stats.ats_failed += int(board is not None)
                    else:
                        stats.ats_validated += 1
                else:
                    board = _ats_board_url(row)
                    approved = True

                if board and board not in existing_urls:

                    detected = api_url_for(board)
                    if detected:
                        company.ats_provider = detected[0]
                        company.ats_board_url = board
                    db.add(
                        Source(
                            company_id=company.id,
                            url=board,
                            source_type="ats_api",
                            fetch_tier="ats_api",
                            # Unvalidated or failing boards wait for a human
                            # rather than entering the crawl rotation.
                            status="approved" if approved else "pending",
                            crawl_frequency_minutes=720,
                            reliability_score=1.0,
                            next_crawl_at=now if approved else None,
                        )
                    )
                    existing_urls.add(board)
                    stats.sources_by_type["ats_api"] += 1

            # --- per-column sources ----------------------------------------
            for column, source_type, tier, frequency, reliability in COLUMN_SOURCES:
                url = row.data.get(column, "")
                if not url or not url.startswith("http") or url in existing_urls:
                    if url and url in existing_urls:
                        stats.sources_skipped += 1
                    continue

                effective_type = "news_site" if is_news else source_type
                effective_freq = 360 if is_news else frequency

                db.add(
                    Source(
                        # NULL for publications: they cover many companies, and
                        # the crawler resolves each entry to a company itself.
                        company_id=company.id if company is not None else None,
                        url=url,
                        source_type=effective_type,
                        fetch_tier=tier,
                        status="approved",
                        crawl_frequency_minutes=effective_freq,
                        reliability_score=0.8 if is_news else reliability,
                        requires_js=_needs_js(row),
                        next_crawl_at=now,
                    )
                )
                existing_urls.add(url)
                stats.sources_by_type[effective_type] += 1

        _seed_news_apis(db, existing_urls, stats, now)
        await db.commit()

    return stats


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
    candidates = [
        slugify(row.name),
        re.sub(r"[^a-z0-9]", "", name),
    ]
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


def _ats_board_url(row: Row) -> str | None:
    """Find a usable ATS board URL for a row.

    First any URL column that already points at a known board, then a URL
    synthesised from the `ats_used` vendor. The registry gives the vendor name
    but never the board URL, so without the second path no ATS source is ever
    seeded — and that is the single most valuable tier.
    """
    for column in ("career_page", "ats_board", "website", "api_documentation"):
        url = row.data.get(column, "")
        if url and api_url_for(url):
            return url

    vendor = row.data.get("ats_used", "").strip().lower()
    template = _BOARD_TEMPLATES.get(vendor)
    if template is None:
        return None

    candidates = _token_candidates(row)
    return template.format(token=candidates[0]) if candidates else None


async def _discover_ats_board(row: Row) -> str | None:
    """Probe every candidate token and return the first board that responds."""
    vendor = row.data.get("ats_used", "").strip().lower()
    template = _BOARD_TEMPLATES.get(vendor)
    if template is None:
        return None

    for token in _token_candidates(row):
        url = template.format(token=token)
        if await _validate_ats(url):
            return url
    return None


def _needs_js(row: Row) -> bool:
    return "playwright" in row.data.get("crawl_method_recommendation", "").lower()


def _seed_news_apis(db, existing_urls: set[str], stats: Stats, now: datetime) -> None:
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
        if not key or url in existing_urls:
            continue
        db.add(
            Source(
                company_id=None,
                url=url,
                source_type=source_type,
                fetch_tier=tier,
                status="approved",
                crawl_frequency_minutes=frequency,
                reliability_score=0.7,
                next_crawl_at=now,
            )
        )
        existing_urls.add(url)
        stats.sources_by_type[source_type] += 1


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
        f"{stats.companies_skipped} skipped (already exist)"
    )
    print(f"  Sources:   {sum(stats.sources_by_type.values())} inserted ({breakdown})")
    if stats.ats_validated or stats.ats_failed:
        print(
            f"  ATS check: {stats.ats_validated} boards reachable, "
            f"{stats.ats_failed} unreachable (seeded as 'pending')"
        )
    if stats.rows_skipped:
        print(f"  Skipped:   {stats.rows_skipped} rows lacking a website")
    print(f"  Totals now: {companies} companies, {sources} sources")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
