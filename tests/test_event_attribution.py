"""Which company an extracted event is filed against.

A news feed is not about one company. One roundup lists eight departures across
three firms, and the extractor happily returns all eight. Filing them all under
whichever company the article resolved to would publish moves at one investor
as evidence on an unrelated payments company's page — the single claim this
product must never make.

So these tests cover the routing, not the extraction: given events the model
already produced, which company does each one land on.
"""

import uuid

import pytest

from app.companies import matcher
from app.extraction.schemas import ExtractedEvent
from workers import crawl


class FakeCompany:
    def __init__(self, name: str, aliases: list[str] | None = None) -> None:
        self.id = uuid.uuid4()
        self.name = name
        self.aliases = aliases


COMPANIES = [
    FakeCompany("Pine Labs"),
    FakeCompany("Groww"),
    FakeCompany("Razorpay"),
]
BY_ID = {c.id: c for c in COMPANIES}


class FakeSession:
    """Stands in for AsyncSession: the routing only ever calls `get`."""

    async def get(self, _model, ident):
        return BY_ID.get(ident)


def _fixed_index(companies):
    """A `get_index` that serves a compiled index without touching a database."""
    index = matcher.CompanyIndex(companies)

    async def fake_get_index(_db, **_kwargs):
        return index

    return fake_get_index


@pytest.fixture(autouse=True)
def _index(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(matcher, "get_index", _fixed_index(COMPANIES))


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, events: list[ExtractedEvent]):
    """Skip the two LLM calls and record what dedup was asked to store."""
    stored: list[tuple[uuid.UUID, str]] = []

    class Relevant:
        is_relevant = True
        reason = ""

    async def fake_classify(_text):
        return Relevant()

    async def fake_extract(_text, _url):
        return events

    async def fake_dedup(_db, event, company_id, _url, **_kwargs):
        stored.append((company_id, event.title))
        return None, True

    monkeypatch.setattr(crawl, "classify_content", fake_classify)
    monkeypatch.setattr(crawl, "extract_events", fake_extract)
    monkeypatch.setattr(crawl, "deduplicate_event", fake_dedup)
    return stored


def _event(title: str, excerpt: str = "") -> ExtractedEvent:
    return ExtractedEvent(
        event_type="leadership_change",
        title=title,
        evidence_excerpt=excerpt,
        confidence=0.9,
    )


async def test_each_event_is_filed_against_the_company_it_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        _event("Anand Dalmia to leave Groww"),
        _event("Razorpay appoints a new CTO"),
    ]
    stored = _stub_pipeline(monkeypatch, events)

    created, touched = await crawl._extract_and_store(
        FakeSession(),
        "a people-moves roundup mentioning Groww, Razorpay and Pine Labs",
        None,
        "https://entrackr.com/some-roundup",
        route_per_event=True,
    )

    assert created == 2
    filed = {BY_ID[cid].name: title for cid, title in stored}
    assert set(filed) == {"Groww", "Razorpay"}
    assert touched == {c.id for c in COMPANIES if c.name in filed}


async def test_an_event_naming_nobody_tracked_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure this exists to prevent.

    Peak XV is not tracked. The event must not inherit whichever company the
    surrounding article happened to mention.
    """
    stored = _stub_pipeline(monkeypatch, [_event("MD Ishaan Mittal exits Peak XV")])

    created, touched = await crawl._extract_and_store(
        FakeSession(),
        "Peak XV partners depart; Pine Labs raised a round last year",
        None,
        "https://entrackr.com/peak-xv",
        route_per_event=True,
    )

    assert created == 0
    assert touched == set()
    assert stored == []


async def test_an_attached_source_keeps_its_own_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A company blog is about that company, whoever else it name-drops.

    Routing per event here would lose real signal: "we are hiring after our
    Series B" on Razorpay's own blog names no company at all.
    """
    razorpay = BY_ID[[c.id for c in COMPANIES if c.name == "Razorpay"][0]]
    stored = _stub_pipeline(
        monkeypatch, [_event("We are expanding the payments team")]
    )

    created, touched = await crawl._extract_and_store(
        FakeSession(),
        "We are expanding the payments team after partnering with Groww",
        razorpay.id,
        "https://razorpay.com/blog/hiring",
    )

    assert created == 1
    assert stored == [(razorpay.id, "We are expanding the payments team")]
    assert touched == {razorpay.id}


async def test_an_irrelevant_document_costs_no_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Irrelevant:
        is_relevant = False
        reason = "marketing"

    async def fake_classify(_text):
        return Irrelevant()

    async def exploding_extract(_text, _url):  # pragma: no cover - must not run
        raise AssertionError("extractor called after the classifier rejected")

    monkeypatch.setattr(crawl, "classify_content", fake_classify)
    monkeypatch.setattr(crawl, "extract_events", exploding_extract)

    created, touched = await crawl._extract_and_store(
        FakeSession(),
        "buy now, 50% off",
        None,
        "https://example.com",
        route_per_event=True,
    )
    assert (created, touched) == (0, set())


async def test_only_the_title_can_name_the_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A company named in the evidence but not the title is not the subject.

    Observed in a real crawl: "SkyAI recruiting Python developers" was filed
    against LinkedIn because the excerpt said where the post appeared.
    """
    stored = _stub_pipeline(
        monkeypatch,
        [
            _event(
                "SkyAI recruiting Python developers",
                "Posted to the Groww jobs board and shared by Razorpay staff",
            )
        ],
    )

    created, touched = await crawl._extract_and_store(
        FakeSession(), "a hiring roundup", None, "https://x.test", route_per_event=True
    )

    assert (created, touched, stored) == (0, set(), [])


async def test_the_evidence_still_supplies_context_for_a_short_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name in the title is read against the words around it, not alone.

    "Ramp" is an ordinary word, so it needs a corporate cue nearby — and in a
    four-word title the cue is usually in the evidence, not the title.
    """
    stored = _stub_pipeline(
        monkeypatch, [_event("Ramp opens Bengaluru site", "The firm raised $150M")]
    )
    ramp = FakeCompany("Ramp")
    BY_ID[ramp.id] = ramp
    monkeypatch.setattr(
        matcher, "get_index", _fixed_index([*COMPANIES, ramp])
    )

    created, _ = await crawl._extract_and_store(
        FakeSession(), "roundup", None, "https://x.test", route_per_event=True
    )
    assert created == 1
    assert stored[0][0] == ramp.id
