"""Pre-filter, dedup matching, rate limiting, and job classification."""

import pytest

from app.crawler.prefilter import is_relevant, markup_ratio
from app.crawler.rate_limiter import RateLimiter, registrable_domain
from app.extraction.dedup import is_same_event, title_overlap
from app.extraction.models import Event
from app.extraction.schemas import ExtractedEvent
from app.jobs.sync import classify_job_title, content_hash, detect_work_mode

FUNDING_TEXT = (
    "Razorpay has raised $75M in a Series F round led by Sequoia. The company "
    "plans to expand its engineering team in Bengaluru over the next year and "
    "is hiring across backend and infrastructure roles."
) * 2


# --- pre-filter --------------------------------------------------------------


def test_accepts_a_funding_announcement() -> None:
    assert is_relevant(FUNDING_TEXT, "company_blog") is True


def test_rejects_content_that_is_too_short() -> None:
    assert is_relevant("Hiring!", "company_blog") is False


def test_rejects_a_festival_greeting() -> None:
    text = (
        "Happy Diwali to all our customers and partners. May the festival of "
        "lights bring joy and prosperity to your families this year. "
    ) * 3
    assert is_relevant(text, "company_blog") is False


def test_rejects_markup_dumps() -> None:
    assert is_relevant("<div>{}</div>[]" * 40, "company_blog") is False


def test_career_pages_always_pass() -> None:
    """A career page is about hiring whatever words are on it."""
    assert is_relevant("Happy Diwali", "career_page") is True
    assert is_relevant("", "ats_api") is True


def test_a_signal_beats_a_festival_greeting_in_the_same_post() -> None:
    """A post that opens with a greeting and then announces funding is still
    a signal, so include patterns take precedence over exclude ones."""
    text = "Happy Diwali! " + FUNDING_TEXT
    assert is_relevant(text, "company_blog") is True


def test_unmatched_content_passes_to_the_llm() -> None:
    """Losing a real signal is permanent; one wasted classifier call is not."""
    text = "We updated our office coffee machine to a new model this quarter. " * 4
    assert is_relevant(text, "company_blog") is True


def test_markup_ratio_of_prose_is_low() -> None:
    assert markup_ratio("Plain prose with no markup at all.") < 0.05


# --- dedup -------------------------------------------------------------------


def test_identical_titles_fully_overlap() -> None:
    assert title_overlap("Razorpay raises Series F", "Razorpay raises Series F") == 1.0


def test_unrelated_titles_do_not_overlap() -> None:
    assert title_overlap("Razorpay raises Series F", "Zepto opens Pune office") < 0.3


def test_stopwords_do_not_create_false_overlap() -> None:
    assert title_overlap("The new office in the city", "A new office of the year") < 0.6


def _stored(event_type: str, data: dict, title: str = "stored") -> Event:
    return Event(event_type=event_type, title=title, structured_data=data)


def _incoming(event_type: str, data: dict, title: str = "stored") -> ExtractedEvent:
    return ExtractedEvent(event_type=event_type, title=title, structured_data=data)


def test_same_funding_round_within_tolerance_is_a_duplicate() -> None:
    """Reports disagree on amounts (pre/post-money, FX), so exact equality
    would never match."""
    stored = _stored("funding", {"round": "series_f", "amount_usd": 75_000_000})
    incoming = _incoming("funding", {"round": "series_f", "amount_usd": 80_000_000})
    assert is_same_event(incoming, stored) is True


def test_different_funding_amounts_are_separate_events() -> None:
    stored = _stored("funding", {"round": "series_f", "amount_usd": 75_000_000})
    incoming = _incoming("funding", {"round": "series_f", "amount_usd": 200_000_000})
    assert is_same_event(incoming, stored) is False


def test_different_rounds_are_separate_events() -> None:
    stored = _stored("funding", {"round": "series_a", "amount_usd": 10_000_000})
    incoming = _incoming("funding", {"round": "series_b", "amount_usd": 10_000_000})
    assert is_same_event(incoming, stored) is False


def test_same_person_and_role_is_one_hire() -> None:
    stored = _stored(
        "leadership_change", {"person": "Jane Doe", "role": "VP of Engineering"}
    )
    incoming = _incoming(
        "leadership_change", {"person": "jane doe", "role": "VP Engineering"}
    )
    assert is_same_event(incoming, stored) is True


def test_different_people_are_separate_hires() -> None:
    stored = _stored("leadership_change", {"person": "Jane Doe", "role": "CTO"})
    incoming = _incoming("leadership_change", {"person": "John Roe", "role": "CTO"})
    assert is_same_event(incoming, stored) is False


def test_same_city_is_one_office() -> None:
    assert is_same_event(
        _incoming("new_office", {"city": "bengaluru"}),
        _stored("new_office", {"city": "Bengaluru"}),
    )


def test_other_types_fall_back_to_title_overlap() -> None:
    stored = _stored("product_launch", {}, title="Acme launches Payments API v2")
    near = _incoming("product_launch", {}, title="Acme launches Payments API v2 today")
    far = _incoming("product_launch", {}, title="Acme opens Chennai office")
    assert is_same_event(near, stored) is True
    assert is_same_event(far, stored) is False


# --- rate limiter ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards-api.greenhouse.io/v1/boards/x", "greenhouse.io"),
        ("https://jobs.lever.co/acme", "lever.co"),
        ("https://engineering.razorpay.com/feed", "razorpay.com"),
        ("https://zerodha.com", "zerodha.com"),
        # Multi-part suffix: without special-casing, every .co.in site would
        # share one bucket.
        ("https://careers.example.co.in/jobs", "example.co.in"),
    ],
)
def test_registrable_domain_extraction(url: str, expected: str) -> None:
    assert registrable_domain(url) == expected


async def test_limiter_allows_once_then_blocks() -> None:
    limiter = RateLimiter(None, interval=60)
    assert await limiter.acquire("https://example.com/a") is True
    # Same registrable domain, different path — still one bucket.
    assert await limiter.acquire("https://example.com/b") is False


async def test_limiter_separates_domains() -> None:
    limiter = RateLimiter(None, interval=60)
    assert await limiter.acquire("https://one.com") is True
    assert await limiter.acquire("https://two.com") is True


# --- job classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "family", "level"),
    [
        ("Senior Backend Engineer", "engineering", "senior"),
        ("Staff Data Scientist", "data", "staff"),
        ("VP of Engineering", "engineering", "vp"),
        ("Product Designer", "design", "mid"),
        ("Junior QA Engineer", "engineering", "junior"),
        ("Engineering Intern", "engineering", "intern"),
        ("Principal ML Engineer", "data", "principal"),
        ("Account Executive", "sales", "mid"),
        ("Legal Counsel", "legal", "mid"),
    ],
)
def test_job_titles_classify(title: str, family: str, level: str) -> None:
    assert classify_job_title(title) == (family, level)


def test_director_is_not_c_level() -> None:
    """"dire(cto)r" contains "cto"; without word boundaries every Director
    would be classified as an executive."""
    assert classify_job_title("Director of Product") == ("product", "director")


def test_work_mode_detection() -> None:
    assert detect_work_mode("This is a fully remote position") == "remote"
    assert detect_work_mode("Hybrid role, 3 days in office") == "hybrid"
    assert detect_work_mode("Work from office in Bengaluru") == "onsite"
    assert detect_work_mode("We build payment infrastructure") is None
    assert detect_work_mode(None) is None


def test_content_hash_is_stable_and_sensitive() -> None:
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert content_hash(None) == content_hash("")
