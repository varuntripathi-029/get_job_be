"""Scoring engine. Pure functions over model instances — no database."""

from datetime import UTC, datetime, timedelta

from app.extraction.models import Event
from app.scoring.engine import (
    BASE_WEIGHTS,
    classify_source_tier,
    event_weight,
    label_for,
    normalize,
)

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def make_event(**overrides) -> Event:
    defaults = {
        "event_type": "funding",
        "title": "Raised $75M Series C",
        "event_occurred_at": NOW,
        "observed_at": NOW,
        "source_count": 1,
        "evidence": [{"source_url": "https://techcrunch.com/article"}],
    }
    return Event(**{**defaults, **overrides})


# --- normalisation and labels ------------------------------------------------


def test_score_is_bounded_to_0_100() -> None:
    for raw in (-1000, -50, 0, 50, 500, 10_000):
        assert 0.0 <= normalize(raw) <= 100.0


def test_score_increases_monotonically_with_raw() -> None:
    scores = [normalize(raw) for raw in range(0, 200, 10)]
    assert scores == sorted(scores)


def test_labels_follow_the_documented_thresholds() -> None:
    assert label_for(5, has_recent_activity=True) == "low"
    assert label_for(24, has_recent_activity=True) == "low"
    assert label_for(25, has_recent_activity=True) == "moderate"
    assert label_for(49, has_recent_activity=True) == "moderate"
    assert label_for(50, has_recent_activity=True) == "high"
    assert label_for(74, has_recent_activity=True) == "high"
    assert label_for(75, has_recent_activity=True) == "very_high"


def test_dormant_company_is_none_not_low() -> None:
    """A stale signal and no recent activity is dormancy, which a job seeker
    should be able to tell apart from weak activity."""
    assert label_for(5, has_recent_activity=False) == "none"
    assert label_for(5, has_recent_activity=True) == "low"


# --- decay -------------------------------------------------------------------


def test_older_events_score_lower_than_identical_recent_ones() -> None:
    recent = event_weight(make_event(event_occurred_at=NOW), "acme.com", NOW)
    old = event_weight(
        make_event(event_occurred_at=NOW - timedelta(days=120)), "acme.com", NOW
    )
    assert old < recent


def test_decay_halves_the_weight_at_one_half_life() -> None:
    """funding has a 120-day half-life, so a 120-day-old round is worth half."""
    fresh = event_weight(make_event(event_occurred_at=NOW), "acme.com", NOW)
    aged = event_weight(
        make_event(event_occurred_at=NOW - timedelta(days=120)), "acme.com", NOW
    )
    assert aged == round(fresh * 0.5, 10) or abs(aged - fresh * 0.5) < 1e-9


def test_future_dated_events_do_not_gain_weight() -> None:
    """A source claiming tomorrow's date must not amplify a signal."""
    now_weight = event_weight(make_event(event_occurred_at=NOW), "acme.com", NOW)
    future = event_weight(
        make_event(event_occurred_at=NOW + timedelta(days=30)), "acme.com", NOW
    )
    assert future == now_weight


def test_falls_back_to_observed_at_when_occurrence_is_unknown() -> None:
    event = make_event(event_occurred_at=None, observed_at=NOW)
    assert event_weight(event, "acme.com", NOW) > 0


# --- corroboration and tiers -------------------------------------------------


def test_more_sources_increase_weight() -> None:
    one = event_weight(make_event(source_count=1), "acme.com", NOW)
    five = event_weight(make_event(source_count=5), "acme.com", NOW)
    assert five > one


def test_corroboration_saturates_at_five_sources() -> None:
    five = event_weight(make_event(source_count=5), "acme.com", NOW)
    fifty = event_weight(make_event(source_count=50), "acme.com", NOW)
    assert five == fifty


def test_first_party_evidence_outweighs_a_rumour() -> None:
    first_party = event_weight(
        make_event(evidence=[{"source_url": "https://acme.com/blog/funding"}]),
        "acme.com",
        NOW,
    )
    rumor = event_weight(make_event(evidence=[]), "acme.com", NOW)
    assert first_party > rumor


def test_best_evidence_tier_wins_over_the_others() -> None:
    """One first-party confirmation settles a claim regardless of how many
    aggregators also repeated it."""
    mixed = event_weight(
        make_event(
            evidence=[
                {"source_url": "https://newsapi.org/x"},
                {"source_url": "https://acme.com/news"},
            ],
            source_count=2,
        ),
        "acme.com",
        NOW,
    )
    aggregator_only = event_weight(
        make_event(
            evidence=[
                {"source_url": "https://newsapi.org/x"},
                {"source_url": "https://gnews.io/y"},
            ],
            source_count=2,
        ),
        "acme.com",
        NOW,
    )
    assert mixed > aggregator_only


def test_ats_boards_count_as_first_party() -> None:
    """Only the company can post to its own board, whoever hosts it."""
    assert classify_source_tier(
        "https://boards.greenhouse.io/acme", "acme.com"
    ) == "first_party"
    assert (
        classify_source_tier("https://jobs.lever.co/acme", "acme.com")
        == "first_party"
    )


def test_unknown_source_is_a_rumour() -> None:
    assert classify_source_tier(None, "acme.com") == "rumor"


# --- weights -----------------------------------------------------------------


def test_layoffs_subtract_from_momentum() -> None:
    assert BASE_WEIGHTS["layoff"] < 0
    assert event_weight(make_event(event_type="layoff"), "acme.com", NOW) < 0


def test_funding_outweighs_a_partnership() -> None:
    funding = event_weight(make_event(event_type="funding"), "acme.com", NOW)
    partnership = event_weight(make_event(event_type="partnership"), "acme.com", NOW)
    assert funding > partnership


def test_unknown_event_type_contributes_nothing() -> None:
    weight = event_weight(make_event(event_type="not_a_real_type"), "acme.com", NOW)
    assert weight == 0.0


def test_scoring_is_deterministic() -> None:
    """The whole point of the engine: same events in, same number out."""
    event = make_event(source_count=3)
    runs = {event_weight(event, "acme.com", NOW) for _ in range(20)}
    assert len(runs) == 1
