"""Resolving which tracked company a headline is about.

These tests pin the asymmetry the module is built around: missing a mention
costs one signal, but a false match attaches someone else's funding round to a
company and publishes it as evidence. Where the two conflict, the false match
is the one to avoid.
"""

import uuid

import pytest

from app.companies.matcher import AMBIGUOUS_NAMES, CompanyIndex


class FakeCompany:
    """Enough of a Company for the index — it only reads id, name, aliases."""

    def __init__(self, name: str, aliases: list[str] | None = None) -> None:
        self.id = uuid.uuid4()
        self.name = name
        self.aliases = aliases


@pytest.fixture
def index() -> CompanyIndex:
    return CompanyIndex(
        [
            FakeCompany("Razorpay"),
            FakeCompany("Swiggy"),
            FakeCompany("Zomato", ["Eternal"]),
            FakeCompany("Blinkit"),
            FakeCompany("Ola"),
            FakeCompany("Ola Electric"),
            FakeCompany("Sarvam", ["Sarvam AI"]),
            FakeCompany("Open"),
            FakeCompany("Linear"),
            FakeCompany("Meta"),
            FakeCompany("Netflix"),
        ]
    )


def names(index: CompanyIndex, text: str) -> list[str]:
    return [m.name for m in index.find(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Razorpay raises $75M in a Series F round", "Razorpay"),
        ("Swiggy is expanding its Bengaluru engineering team", "Swiggy"),
        ("Netflix has opened a new engineering hub", "Netflix"),
        # The alias is the only spelling in the text.
        ("Eternal reported a profitable quarter", "Zomato"),
        ("Sarvam AI opens a research office in Bengaluru", "Sarvam"),
        # Short but real, so held to the stricter rule rather than dropped.
        ("Ola raises a fresh round from existing investors", "Ola"),
        # Punctuation and possessives still bound the name.
        ("Funding news: Razorpay's valuation doubles", "Razorpay"),
    ],
)
def test_finds_the_company(index: CompanyIndex, text: str, expected: str) -> None:
    assert names(index, text)[:1] == [expected]


@pytest.mark.parametrize(
    "text",
    [
        # A name that is also an ordinary word, used as an ordinary word.
        "How to build a linear regression model in Python",
        "The meta description tag still matters for SEO",
        "Our team is hiring engineers to work on open source infrastructure",
        "We reduced open connections after the migration, and are hiring",
        # Right casing, but nothing to suggest the sentence is about a company.
        "Open the dashboard and select a date range",
        # No tracked company at all.
        "A guide to writing better commit messages",
        "",
    ],
)
def test_does_not_invent_a_company(index: CompanyIndex, text: str) -> None:
    assert names(index, text) == []


def test_ambiguous_name_matches_when_used_as_a_name(index: CompanyIndex) -> None:
    """The strict rule must not silence the company outright."""
    assert names(index, "Open raised $25M in a round led by Tiger") == ["Open"]
    assert names(index, "Linear has appointed a new CTO") == ["Linear"]


def test_ambiguous_names_are_actually_marked_ambiguous() -> None:
    """A guard on the list itself: these are the names the rule exists for."""
    assert {"open", "linear", "meta", "slice", "ramp"} <= AMBIGUOUS_NAMES
    # Not English words, so holding them to the strict rule only loses signal.
    assert not ({"groww", "razorpay", "swiggy", "rapido"} & AMBIGUOUS_NAMES)


def test_longest_name_wins_over_its_own_prefix(index: CompanyIndex) -> None:
    """"Ola Electric" is a different company from "Ola"."""
    assert names(index, "Ola Electric reports record deliveries") == ["Ola Electric"]
    assert names(index, "Ola, the ride-hailing company, is piloting an app") == ["Ola"]


def test_the_subject_outranks_a_company_named_in_passing(
    index: CompanyIndex,
) -> None:
    """An acquisition is expansion for the acquirer, so it must rank first."""
    ranked = names(index, "Zomato acquires Blinkit in an all-stock deal")
    assert ranked[0] == "Zomato"
    assert "Blinkit" in ranked

    # Repetition is how the caller weights a headline over the body.
    body = "Blinkit. Blinkit. Zomato said its subsidiary Blinkit will expand."
    assert names(index, body)[0] == "Blinkit"


def test_a_name_two_companies_share_identifies_neither() -> None:
    """Better to lose the signal than to attribute it to a coin flip."""
    index = CompanyIndex([FakeCompany("Apex"), FakeCompany("Apex")])
    assert index.find("Apex raised a Series B") == []


def test_names_too_short_to_mean_anything_are_never_indexed() -> None:
    """"X" and "Fi" match constantly and carry no meaning in prose."""
    index = CompanyIndex([FakeCompany("X"), FakeCompany("Fi")])
    assert index.find("X and Fi raised a round") == []


def test_an_alias_and_the_name_count_as_one_company(index: CompanyIndex) -> None:
    """Otherwise a company beats itself and the ranking is meaningless."""
    mentions = index.find("Zomato said Eternal, its parent, will expand hiring")
    assert [m.name for m in mentions] == ["Zomato"]
    assert mentions[0].count == 2


def test_a_domain_does_not_match_a_company_by_its_suffix() -> None:
    index = CompanyIndex([FakeCompany("Meta"), FakeCompany("Netflix")])
    # "meta" inside ai.meta.com must not count as a mention.
    assert index.find("Read more at ai.meta.com for the funding details") == []


def test_empty_index_is_safe() -> None:
    assert CompanyIndex([]).find("Razorpay raised $75M") == []
