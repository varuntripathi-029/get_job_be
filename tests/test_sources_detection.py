"""ATS provider and fetch-tier detection. Pure functions, no I/O."""

import pytest

from app.sources.service import detect_ats_provider, detect_fetch_tier, is_pseudo_url


@pytest.mark.parametrize(
    ("url", "provider", "api_url"),
    [
        (
            "https://boards.greenhouse.io/razorpay",
            "greenhouse",
            "https://boards-api.greenhouse.io/v1/boards/razorpay/jobs?content=true",
        ),
        (
            "https://jobs.lever.co/zepto",
            "lever",
            "https://api.lever.co/v0/postings/zepto?mode=json",
        ),
        (
            "https://jobs.ashbyhq.com/sarvam",
            "ashby",
            "https://api.ashbyhq.com/posting-api/job-board/sarvam"
            "?includeCompensation=true",
        ),
        (
            "https://apply.workable.com/wysa",
            "workable",
            "https://apply.workable.com/api/v1/companies/wysa/jobs",
        ),
    ],
)
def test_detect_ats_provider(url: str, provider: str, api_url: str) -> None:
    assert detect_ats_provider(url) == (provider, api_url)


@pytest.mark.parametrize(
    "url", ["https://example.com/careers", "https://razorpay.com/jobs/"]
)
def test_non_ats_urls_return_none(url: str) -> None:
    assert detect_ats_provider(url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme", "ats_api"),
        ("https://engineering.razorpay.com/feed", "rss"),
        ("https://zerodha.tech/index.xml", "rss"),
        ("https://example.com/careers", "static_http"),
        ("newsapi://search", "news_api"),
        ("gnews://search", "news_api"),
        ("serpapi://bing_news", "search_api"),
    ],
)
def test_detect_fetch_tier(url: str, expected: str) -> None:
    assert detect_fetch_tier(url) == expected


def test_pseudo_url_detection() -> None:
    assert is_pseudo_url("newsapi://search") is True
    assert is_pseudo_url("https://example.com") is False
