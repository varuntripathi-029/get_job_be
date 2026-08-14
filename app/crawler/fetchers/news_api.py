"""Third-party news and search APIs.

Unlike every other tier, these are not tied to one company: one call searches for
whichever companies are due, so `sources.company_id` is NULL and the crawl task
resolves each article back to a company afterwards.

They are also the only tier with a hard daily budget — NewsAPI and GNews allow
100 requests/day, SerpAPI 250/month — so quota tracking gates every call. These
URLs are pseudo-URLs (`newsapi://search`) pointing at trusted vendor endpoints,
so SSRF validation and the politeness limiter deliberately do not apply.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.config import settings
from app.crawler.fetchers.base import (
    DEFAULT_TIMEOUT,
    BaseFetcher,
    FetchResult,
    content_hash,
    default_headers,
)

logger = logging.getLogger(__name__)

# Vendor free-tier ceilings. Tracked locally so we stop before they start
# returning errors, which keeps the failure counters meaningful.
DAILY_LIMITS = {"newsapi": 100, "gnews": 100, "serpapi": 8}

ARTICLES_PER_COMPANY = 10
# Terms that make an article plausibly a hiring signal. Without them a query for
# a consumer brand returns product reviews and stock commentary.
SIGNAL_TERMS = "hiring OR funding OR raised OR expansion OR launch OR acquired"


@dataclass(slots=True)
class NewsArticle:
    title: str
    description: str
    content: str
    url: str
    published_at: datetime | None
    source_name: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_at"] = (
            self.published_at.isoformat() if self.published_at else None
        )
        return data

    @property
    def text(self) -> str:
        """Everything an extractor should see for this article."""
        return "\n\n".join(p for p in (self.title, self.description, self.content) if p)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class QuotaTracker:
    """Daily per-API call budget.

    Redis-backed so it survives worker restarts, with an in-memory fallback.
    Three Upstash commands per API per tick (GET, INCR, EXPIRE) — negligible
    against the 10,000/day budget, and it protects a much scarcer resource.
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._memory: dict[str, int] = {}

    @staticmethod
    def _key(api: str) -> str:
        return f"qu:{api}:{datetime.now(UTC).strftime('%m%d')}"

    async def used(self, api: str) -> int:
        key = self._key(api)
        if self.redis is None:
            return self._memory.get(key, 0)
        try:
            return int(await self.redis.get(key) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("quota read failed for %s (%s); assuming 0", api, exc)
            return 0

    async def allowed(self, api: str) -> bool:
        return await self.used(api) < DAILY_LIMITS.get(api, 100)

    async def record(self, api: str) -> None:
        key = self._key(api)
        if self.redis is None:
            self._memory[key] = self._memory.get(key, 0) + 1
            return
        try:
            await self.redis.incr(key)
            # 48h, so a key set just before midnight still expires on its own.
            await self.redis.expire(key, 48 * 3600)
        except Exception as exc:  # noqa: BLE001
            logger.warning("quota write failed for %s: %s", api, exc)

    @staticmethod
    def resets_at() -> datetime:
        """When the daily buckets roll over.

        `_key` is stamped with the UTC date, so every counter resets together
        at UTC midnight rather than on a per-key sliding window.
        """
        now = datetime.now(UTC)
        return (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def status(self) -> list[dict[str, object]]:
        """Per-vendor usage, for showing users why crawling has paused."""
        rows = []
        for api, limit in DAILY_LIMITS.items():
            used = await self.used(api)
            rows.append(
                {
                    "api": api,
                    "used": used,
                    "limit": limit,
                    "remaining": max(0, limit - used),
                    "exhausted": used >= limit,
                }
            )
        return rows


class NewsAPIFetcher(BaseFetcher):
    def __init__(self, quota: QuotaTracker | None = None, companies=None):
        self.quota = quota or QuotaTracker()
        # Set by the crawl task to the batch of companies due for a news sweep.
        self.companies: list[tuple[str, list[str] | None]] = companies or []

    async def fetch(self, url: str) -> FetchResult:
        """Search news for the configured company batch.

        `url` is a pseudo-URL identifying the source row; the batch decides what
        is actually queried.
        """
        started = time.monotonic()
        articles: list[NewsArticle] = []

        for name, aliases in self.companies:
            articles.extend(await self.fetch_company_news(name, aliases))

        # The same story reaches us from several vendors; one URL is one story.
        unique: dict[str, NewsArticle] = {}
        for article in articles:
            if article.url and article.url not in unique:
                unique[article.url] = article

        payload = json.dumps([a.to_dict() for a in unique.values()], sort_keys=True)
        elapsed = int((time.monotonic() - started) * 1000)
        logger.info(
            "news sweep over %d companies produced %d unique articles",
            len(self.companies),
            len(unique),
        )
        return FetchResult(
            content=payload,
            content_hash=content_hash(payload),
            http_status=200,
            content_type="application/json",
            duration_ms=elapsed,
        )

    async def fetch_company_news(
        self, company_name: str, aliases: list[str] | None = None
    ) -> list[NewsArticle]:
        """Query every configured vendor for one company."""
        query = f'"{company_name}" AND ({SIGNAL_TERMS})'
        articles: list[NewsArticle] = []

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            if settings.newsapi_key and await self.quota.allowed("newsapi"):
                articles += await self._newsapi(client, query)
                await self.quota.record("newsapi")
            if settings.gnews_api_key and await self.quota.allowed("gnews"):
                articles += await self._gnews(client, query)
                await self.quota.record("gnews")
            # Job listings before news. On the free tier this budget is tiny
            # (see DAILY_LIMITS), and an open role is a harder hiring signal
            # than an article speculating about one, so it gets first call on
            # whatever is left.
            if settings.serpapi_key and await self.quota.allowed("serpapi"):
                articles += await self._serpapi_jobs(client, company_name)
                await self.quota.record("serpapi")
            if settings.serpapi_key and await self.quota.allowed("serpapi"):
                articles += await self._serpapi(client, company_name)
                await self.quota.record("serpapi")

        return articles

    async def _newsapi(
        self, client: httpx.AsyncClient, query: str
    ) -> list[NewsArticle]:
        try:
            response = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": ARTICLES_PER_COMPANY,
                    "apiKey": settings.newsapi_key,
                },
                headers=default_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("newsapi request failed: %s", exc)
            return []

        return [
            NewsArticle(
                title=a.get("title") or "",
                description=a.get("description") or "",
                content=a.get("content") or "",
                url=a.get("url") or "",
                published_at=_parse_date(a.get("publishedAt")),
                source_name=(a.get("source") or {}).get("name") or "newsapi",
            )
            for a in response.json().get("articles", [])
        ]

    async def _gnews(self, client: httpx.AsyncClient, query: str) -> list[NewsArticle]:
        try:
            response = await client.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": query,
                    "lang": "en",
                    "max": ARTICLES_PER_COMPANY,
                    "apikey": settings.gnews_api_key,
                },
                headers=default_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("gnews request failed: %s", exc)
            return []

        return [
            NewsArticle(
                title=a.get("title") or "",
                description=a.get("description") or "",
                content=a.get("content") or "",
                url=a.get("url") or "",
                published_at=_parse_date(a.get("publishedAt")),
                source_name=(a.get("source") or {}).get("name") or "gnews",
            )
            for a in response.json().get("articles", [])
        ]

    async def _serpapi_jobs(
        self, client: httpx.AsyncClient, company_name: str
    ) -> list[NewsArticle]:
        """Google Jobs listings for a company.

        The coverage path for companies with neither a hosted ATS board nor a
        parseable careers page — which, measured on a sample of six real
        careers pages, is most of them. Google already aggregates postings from
        the boards those companies do use, so one query reaches sites we cannot
        crawl directly and must not scrape.

        Returned as NewsArticles because that is what this fetcher emits; the
        crawl path routes them through the same extractor as any other signal.
        """
        try:
            response = await client.get(
                "https://serpapi.com/search",
                params={
                    "engine": "google_jobs",
                    "q": f"{company_name} jobs",
                    "hl": "en",
                    "api_key": settings.serpapi_key,
                },
                headers=default_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("serpapi google_jobs request failed: %s", exc)
            return []

        results = response.json().get("jobs_results") or []
        articles = []
        for r in results[:ARTICLES_PER_COMPANY]:
            title = r.get("title") or ""
            if not title:
                continue
            # apply_options carries the real board link; share_link is a Google
            # redirect that expires.
            apply_options = r.get("apply_options") or []
            url = ""
            if apply_options and isinstance(apply_options[0], dict):
                url = apply_options[0].get("link") or ""
            url = url or r.get("share_link") or ""
            if not url:
                continue
            articles.append(
                NewsArticle(
                    title=f"{r.get('company_name') or company_name} is hiring: {title}",
                    description=(r.get("location") or ""),
                    content=(r.get("description") or "")[:2000],
                    url=url,
                    published_at=None,
                    source_name=r.get("via") or "google_jobs",
                )
            )
        logger.info(
            "google_jobs returned %d listings for %s", len(articles), company_name
        )
        return articles

    async def _serpapi(
        self, client: httpx.AsyncClient, company_name: str
    ) -> list[NewsArticle]:
        try:
            response = await client.get(
                "https://serpapi.com/search",
                params={
                    "engine": "bing_news",
                    "q": f'"{company_name}" hiring OR funding OR expansion',
                    "api_key": settings.serpapi_key,
                },
                headers=default_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("serpapi request failed: %s", exc)
            return []

        payload = response.json()
        results = payload.get("organic_results") or payload.get("news_results") or []
        return [
            NewsArticle(
                title=r.get("title") or "",
                description=r.get("snippet") or "",
                content="",
                url=r.get("link") or "",
                published_at=_parse_date(r.get("date")),
                source_name=r.get("source") or "serpapi",
            )
            for r in results
        ]
