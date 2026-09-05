"""Admin source management: re-detect tier and delete.

DB-backed, so these skip when no test Postgres is reachable (see conftest).
"""

import pytest

from app.common.exceptions import NotFoundError
from app.sources.models import Source
from app.sources.service import delete_source, redetect_fetch_tier


async def _make_source(db, **overrides) -> Source:
    defaults = dict(
        url="https://blitznow.keka.com/careers/",
        source_type="career_page",
        fetch_tier="static_http",
        status="approved",
    )
    defaults.update(overrides)
    source = Source(**defaults)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def test_redetect_moves_a_keka_source_to_the_ats_tier(db_session) -> None:
    # A Keka careers page registered before the adapter existed, stuck on the
    # static tier and backed off after failures.
    source = await _make_source(
        db_session, fetch_tier="static_http", consecutive_failures=5
    )

    updated = await redetect_fetch_tier(db_session, source.id)

    assert updated.fetch_tier == "ats_api"
    # Approved, so it is rescheduled now and the failure backoff is cleared.
    assert updated.consecutive_failures == 0
    assert updated.next_crawl_at is not None


async def test_redetect_leaves_a_plain_page_on_static(db_session) -> None:
    source = await _make_source(
        db_session, url="https://acme.example.com/careers", fetch_tier="static_http"
    )

    updated = await redetect_fetch_tier(db_session, source.id)

    assert updated.fetch_tier == "static_http"


async def test_delete_removes_the_source(db_session) -> None:
    source = await _make_source(db_session)

    await delete_source(db_session, source.id)

    with pytest.raises(NotFoundError):
        await redetect_fetch_tier(db_session, source.id)
