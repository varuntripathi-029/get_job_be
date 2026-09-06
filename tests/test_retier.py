"""The fetch-tier re-sync sweep and approval-time upgrade.

`_upgrade_to_ats_tier` is a pure function over a Source, so those tests run
everywhere. `resync_fetch_tiers` and `approve_source` are DB-backed and skip
when no test Postgres is reachable (see conftest).
"""

import uuid

from app.sources.models import Source
from app.sources.service import (
    _upgrade_to_ats_tier,
    approve_source,
    resync_fetch_tiers,
)


def _source(url: str, fetch_tier: str, **overrides) -> Source:
    return Source(
        url=url,
        source_type=overrides.pop("source_type", "career_page"),
        fetch_tier=fetch_tier,
        status=overrides.pop("status", "approved"),
        **overrides,
    )


def test_upgrade_promotes_a_keka_page_off_the_static_tier() -> None:
    source = _source("https://blitznow.keka.com/careers/", "static_http")

    changed = _upgrade_to_ats_tier(source)

    assert changed is True
    assert source.fetch_tier == "ats_api"


def test_upgrade_is_a_noop_for_a_plain_careers_page() -> None:
    source = _source("https://acme.example.com/careers", "static_http")

    changed = _upgrade_to_ats_tier(source)

    assert changed is False
    assert source.fetch_tier == "static_http"


def test_upgrade_is_a_noop_when_already_on_the_ats_tier() -> None:
    source = _source("https://blitznow.keka.com/careers/", "ats_api")

    assert _upgrade_to_ats_tier(source) is False


def test_upgrade_ignores_an_unparseable_ats_vendor() -> None:
    # Recognised as an ATS host but with no response parser, so it must not be
    # promoted to a tier whose fetcher would return nothing on every crawl.
    source = _source("https://acme.workable.com/", "static_http")

    assert _upgrade_to_ats_tier(source) is False
    assert source.fetch_tier == "static_http"


def test_upgrade_skips_pseudo_urls() -> None:
    source = _source("newsapi://search", "news_api", source_type="news_api")

    assert _upgrade_to_ats_tier(source) is False


async def test_sweep_promotes_only_the_stale_ats_source(db_session) -> None:
    keka = Source(
        url="https://blitznow.keka.com/careers/",
        source_type="career_page",
        fetch_tier="static_http",
        status="approved",
        consecutive_failures=4,
    )
    plain = Source(
        url="https://acme.example.com/careers",
        source_type="career_page",
        fetch_tier="static_http",
        status="approved",
    )
    db_session.add_all([keka, plain])
    await db_session.commit()

    result = await resync_fetch_tiers(db_session)

    assert result["upgraded"] == 1
    await db_session.refresh(keka)
    await db_session.refresh(plain)
    assert keka.fetch_tier == "ats_api"
    # Promoted, so rescheduled now with the failure backoff cleared.
    assert keka.consecutive_failures == 0
    assert keka.next_crawl_at is not None
    assert plain.fetch_tier == "static_http"


async def test_approval_upgrades_a_stale_ats_source(db_session) -> None:
    source = Source(
        url="https://blitznow.keka.com/careers/",
        source_type="career_page",
        fetch_tier="static_http",
        status="pending",
    )
    db_session.add(source)
    await db_session.commit()

    approved = await approve_source(db_session, uuid.uuid4(), source.id)

    assert approved.status == "approved"
    assert approved.fetch_tier == "ats_api"
