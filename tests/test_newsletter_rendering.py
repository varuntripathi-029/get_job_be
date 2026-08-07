"""Newsletter rendering and embedding-input construction."""

from datetime import UTC, datetime

from app.embeddings import build_job_embedding_text, build_resume_embedding_text
from app.newsletter.schemas import (
    CompanyEntry,
    EventEntry,
    HotspotEntry,
    MoverEntry,
    NewsletterContent,
)
from app.newsletter.templates import (
    format_date,
    render_confirmation_email,
    render_newsletter_html,
)

BASE = "https://hiresignal.example"
UNSUB = f"{BASE}/newsletter/unsubscribe?token=abc"


def make_content(**overrides) -> NewsletterContent:
    defaults = {
        "subject": "HireSignal Weekly — August 7, 2026",
        "top_movers": [
            MoverEntry("Razorpay", "razorpay", 82.0, "very_high", 14.0),
        ],
        "hiring_hotspots": [HotspotEntry("Zepto", "zepto", 12)],
        "notable_events": [
            EventEntry(
                company_name="Razorpay",
                company_slug="razorpay",
                event_type="funding",
                title="Raised $75M Series F",
                occurred_at=datetime(2026, 8, 5, tzinfo=UTC),
                evidence_url="https://example.com/news",
            )
        ],
        "new_entrants": [CompanyEntry("Sarvam AI", "sarvam-ai", "AI")],
        "generated_at": datetime(2026, 8, 7, tzinfo=UTC),
        "edition_number": 4,
    }
    return NewsletterContent(**{**defaults, **overrides})


def test_renders_every_section() -> None:
    html = render_newsletter_html(make_content(), UNSUB, BASE)
    assert "Top movers" in html
    assert "Hiring hotspots" in html
    assert "Notable events" in html
    assert "Now tracking" in html
    assert "Razorpay" in html
    assert "12 new roles" in html


def test_includes_the_unsubscribe_link_and_disclaimer() -> None:
    html = render_newsletter_html(make_content(), UNSUB, BASE)
    assert UNSUB in html
    assert "Unsubscribe" in html
    # The product guardrail: never claim a company will hire.
    assert "not predictions" in html


def test_company_links_point_at_the_frontend() -> None:
    html = render_newsletter_html(make_content(), UNSUB, BASE)
    assert f"{BASE}/companies/razorpay" in html


def test_escapes_company_names() -> None:
    """Names come from crawled pages, so they are untrusted input in HTML."""
    content = make_content(
        top_movers=[
            MoverEntry("<script>alert(1)</script>", "x", 50.0, "high", 5.0)
        ]
    )
    html = render_newsletter_html(content, UNSUB, BASE)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_sections_are_omitted_entirely() -> None:
    content = make_content(
        top_movers=[], hiring_hotspots=[], notable_events=[], new_entrants=[]
    )
    html = render_newsletter_html(content, UNSUB, BASE)
    assert "Top movers" not in html
    assert "Hiring hotspots" not in html
    # The shell still renders, so the email is never malformed.
    assert "HireSignal" in html


def test_is_empty_tracks_the_three_content_sections() -> None:
    assert NewsletterContent(subject="x").is_empty
    # New entrants alone are not worth an email.
    assert NewsletterContent(
        subject="x", new_entrants=[CompanyEntry("A", "a", None)]
    ).is_empty
    assert not NewsletterContent(
        subject="x", hiring_hotspots=[HotspotEntry("A", "a", 1)]
    ).is_empty


def test_singular_role_wording() -> None:
    content = make_content(hiring_hotspots=[HotspotEntry("Zepto", "zepto", 1)])
    assert "1 new role" in render_newsletter_html(content, UNSUB, BASE)


def test_negative_delta_keeps_its_sign() -> None:
    content = make_content(
        top_movers=[MoverEntry("A", "a", 40.0, "low", -6.0)]
    )
    html = render_newsletter_html(content, UNSUB, BASE)
    assert "(-6)" in html


def test_confirmation_email_carries_the_link_twice() -> None:
    """Once as a button, once as pasteable text for clients that strip links."""
    url = "https://hiresignal.example/newsletter/confirm?token=xyz"
    html = render_confirmation_email(url)
    assert html.count(url) == 2
    assert "48 hours" in html


def test_format_date_is_platform_independent() -> None:
    assert format_date(datetime(2026, 8, 7, tzinfo=UTC)) == "August 7, 2026"
    assert format_date(datetime(2026, 12, 25, tzinfo=UTC)) == "December 25, 2026"
    assert format_date(None) == ""


# --- Embedding input ---------------------------------------------------------


def test_resume_embedding_text_joins_parsed_fields() -> None:
    text = build_resume_embedding_text(
        ["Python", "React"], ["engineering"], "senior", ["Bengaluru"]
    )
    assert text == "Python React engineering senior Bengaluru"


def test_resume_embedding_text_skips_missing_fields() -> None:
    assert build_resume_embedding_text(["Python"], None, None, None) == "Python"
    assert build_resume_embedding_text(None, None, None, None) == ""


def test_job_embedding_text_truncates_the_description() -> None:
    text = build_job_embedding_text("Engineer", "Platform", "engineering", "x" * 2000)
    # Descriptions end in identical benefits boilerplate; the cap keeps that
    # from washing out the parts that distinguish one role from another.
    assert text.count("x") == 500
