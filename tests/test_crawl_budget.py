"""Daily third-party lookup budget, and what users are told when it runs out."""

from datetime import UTC, datetime

import pytest

from app.crawler.fetchers.news_api import DAILY_LIMITS, QuotaTracker


class TestQuotaTracker:
    @pytest.mark.asyncio
    async def test_starts_unused(self):
        tracker = QuotaTracker()
        assert await tracker.used("serpapi") == 0
        assert await tracker.allowed("serpapi") is True

    @pytest.mark.asyncio
    async def test_records_usage(self):
        tracker = QuotaTracker()
        await tracker.record("serpapi")
        await tracker.record("serpapi")
        assert await tracker.used("serpapi") == 2

    @pytest.mark.asyncio
    async def test_blocks_once_the_limit_is_reached(self):
        tracker = QuotaTracker()
        for _ in range(DAILY_LIMITS["serpapi"]):
            await tracker.record("serpapi")
        assert await tracker.allowed("serpapi") is False

    @pytest.mark.asyncio
    async def test_vendors_have_separate_budgets(self):
        tracker = QuotaTracker()
        for _ in range(DAILY_LIMITS["serpapi"]):
            await tracker.record("serpapi")
        assert await tracker.allowed("serpapi") is False
        assert await tracker.allowed("gnews") is True

    @pytest.mark.asyncio
    async def test_status_reports_every_vendor(self):
        tracker = QuotaTracker()
        await tracker.record("serpapi")
        rows = {row["api"]: row for row in await tracker.status()}
        assert rows["serpapi"]["used"] == 1
        assert rows["serpapi"]["remaining"] == DAILY_LIMITS["serpapi"] - 1
        assert rows["serpapi"]["exhausted"] is False

    @pytest.mark.asyncio
    async def test_status_flags_exhaustion(self):
        tracker = QuotaTracker()
        for _ in range(DAILY_LIMITS["serpapi"]):
            await tracker.record("serpapi")
        rows = {row["api"]: row for row in await tracker.status()}
        assert rows["serpapi"]["exhausted"] is True
        assert rows["serpapi"]["remaining"] == 0

    def test_resets_at_next_utc_midnight(self):
        resets = QuotaTracker.resets_at()
        now = datetime.now(UTC)
        assert resets > now
        assert (resets.hour, resets.minute, resets.second) == (0, 0, 0)
        # The keys are stamped with the UTC date, so the rollover is within a day.
        assert (resets - now).total_seconds() <= 24 * 3600
