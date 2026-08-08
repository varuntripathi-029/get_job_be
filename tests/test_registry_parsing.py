"""Parsing rules for the markdown source registry.

The registry is hand-maintained, so these tests pin the judgement calls that
decide what gets seeded: what counts as a URL, which name a row is really
about, and which section a row belongs to.
"""

from pathlib import Path

import pytest

from scripts import registry

REGISTRY = (
    Path(__file__).resolve().parent.parent / "hiring_intelligence_source_registry.md"
)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        # The current registry omits the scheme on every URL.
        ("inc42.com/feed", "https://inc42.com/feed"),
        ("https://inc42.com/feed", "https://inc42.com/feed"),
        (
            "medium.com/feed/airbnb-engineering",
            "https://medium.com/feed/airbnb-engineering",
        ),
        # Nulls, in the several spellings the registry uses.
        ("N/A", ""),
        ("", ""),
        ("-", ""),
        # Annotated cells are not crawl targets. Seeding these would produce a
        # source that 404s forever, which is exactly what the annotation warns
        # about.
        ("sebi.gov.in (no confirmed public RSS — verify before use)", ""),
        ("tech.flipkart.com (via Medium/GitHub eng blog network)", ""),
        ("N/A (use registry.npmjs.org API, no RSS)", ""),
        ("ycombinator.com/companies (public dataset)", ""),
        # Prose that happens to contain a dot is not a URL.
        ("No documented public job-board API", ""),
        ("Yes (open source)", ""),
    ],
)
def test_normalize_url(cell: str, expected: str) -> None:
    assert registry.normalize_url(cell) == expected


@pytest.mark.parametrize(
    ("raw", "section", "expected"),
    [
        # The engineering-blog section titles rows after the publication. The
        # entity we track is the company, and an article says "Netflix".
        ("Netflix TechBlog", "global_engineering_blogs", "Netflix"),
        ("Stripe Blog (Engineering)", "global_engineering_blogs", "Stripe"),
        ("Amazon Science", "global_engineering_blogs", "Amazon"),
        ("Apple Machine Learning Research", "global_engineering_blogs", "Apple"),
        ("Mozilla Hacks", "global_engineering_blogs", "Mozilla"),
        # Everywhere else the name is already the company and must survive
        # untouched — "Krutrim" is not a blog suffix.
        ("Zomato (Eternal)", "indian_unicorns", "Zomato (Eternal)"),
        ("Krutrim", "indian_unicorns", "Krutrim"),
    ],
)
def test_company_name(raw: str, section: str, expected: str) -> None:
    assert registry.company_name(raw, section) == expected


@pytest.mark.parametrize(
    ("raw", "section", "expected"),
    [
        # The press uses both names interchangeably.
        ("Zomato (Eternal)", "indian_unicorns", {"eternal", "zomato"}),
        ("API Holdings (PharmEasy)", "indian_unicorns", {"pharmeasy", "api holdings"}),
        (
            "Z47 (formerly Matrix Partners India)",
            "vcs",
            {"matrix partners india", "z47"},
        ),
        ("Info Edge / Naukri.com", "indian_startups", {"info edge", "naukri.com"}),
        # A qualifier that only disambiguates the row is not a name.
        ("Turing (India ops)", "indian_startups", {"turing"}),
        ("Zscaler India R&D", "indian_startups", {"zscaler"}),
        # A row with nothing to add produces nothing.
        ("Razorpay", "indian_unicorns", set()),
    ],
)
def test_name_aliases(raw: str, section: str, expected: set[str]) -> None:
    assert {a.lower() for a in registry.name_aliases(raw, section)} == expected


def test_generic_words_never_become_aliases() -> None:
    """A generic alias would match nearly every article.

    "Stripe Blog (Engineering)" must not teach the matcher that the word
    "Engineering" identifies Stripe.
    """
    for raw in (
        "Stripe Blog (Engineering)",
        "Freshworks Neo (subsidiary lines)",
        "Forbes India (Startups)",
        "Sequoia Capital (Global/US)",
    ):
        aliases = {a.lower() for a in registry.name_aliases(raw, "indian_startups")}
        assert aliases & registry._GENERIC_NAMES == set(), raw


def test_headers_are_aliased_across_both_registry_formats() -> None:
    """"Career Pg" and "Career Page" have to land on the same key."""
    short = registry._map_headers(["Name", "Career Pg", "Eng Blog", "API Docs"])
    assert short == ["name", "career_page", "engineering_blog", "api_documentation"]

    full = registry._map_headers(["Name", "Career Page", "Engineering Blog"])
    assert full[:3] == ["name", "career_page", "engineering_blog"]


def test_colliding_headers_stay_addressable() -> None:
    """github_orgs has both a bare handle and a URL under GitHub-ish names.

    The rightmost is the specific one, so it keeps the canonical key.
    """
    mapped = registry._map_headers(["GitHub Org", "Parent Company", "GitHub URL"])
    assert mapped == ["_github_organization", "name", "github_organization"]


# --- against the real file ---------------------------------------------------


@pytest.fixture(scope="module")
def rows() -> list[registry.Row]:
    return registry.parse(REGISTRY)


def test_every_section_is_classified(rows: list[registry.Row]) -> None:
    known = (
        registry.COMPANY_SECTIONS
        | registry.NEWS_SECTIONS
        | registry.ENRICHMENT_SECTIONS
    )
    assert {row.section for row in rows} <= known


def test_registry_yields_companies_and_sources(rows: list[registry.Row]) -> None:
    assert len(rows) > 400
    companies = [r for r in rows if r.is_company]
    assert len(companies) > 250
    # The registry's value is that most rows have no career page — those
    # companies are tracked through what is written about them. If this ever
    # inverts, the parser is picking up something it shouldn't.
    without_careers = [r for r in companies if not r.url("career_page")]
    assert len(without_careers) > len(companies) / 3


def test_no_annotated_cell_becomes_a_source(rows: list[registry.Row]) -> None:
    for row in rows:
        for column in ("career_page", "company_blog", "engineering_blog", "rss_feed"):
            url = row.url(column)
            assert " " not in url, f"{row.section}:{row.lineno} {column}={url!r}"


def test_frequency_and_priority_are_read(rows: list[registry.Row]) -> None:
    assert any(r.frequency_minutes == 1440 for r in rows)
    assert any(r.reliability == 0.9 for r in rows)


def test_old_registry_format_still_parses() -> None:
    """The previous registry must keep working — it is still committed."""
    old = REGISTRY.parent / "source-registry.md"
    if not old.exists():  # pragma: no cover - file may be retired later
        pytest.skip("old registry removed")
    parsed = registry.parse(old)
    assert parsed
    assert any(r.url("rss_feed").startswith("https://") for r in parsed)
