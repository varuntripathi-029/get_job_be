"""Scheduler endpoint auth.

The endpoints spend LLM and third-party API budget on demand, so the gate in
front of them is the only thing between a stranger and your quota.
"""

import pytest
from fastapi import HTTPException

from app.common.exceptions import AuthenticationError
from app.config import settings
from app.scheduler.router import SchedulerDisabledError, require_scheduler_token


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_token", "s3cret-token-value")
    return "s3cret-token-value"


class TestSchedulerToken:
    @pytest.mark.asyncio
    async def test_accepts_the_configured_token(self, token):
        # Returns None rather than raising.
        assert await require_scheduler_token(token) is None

    @pytest.mark.asyncio
    async def test_rejects_a_wrong_token(self, token):
        with pytest.raises(AuthenticationError):
            await require_scheduler_token("not-the-token")

    @pytest.mark.asyncio
    async def test_rejects_a_missing_header(self, token):
        with pytest.raises(AuthenticationError):
            await require_scheduler_token(None)

    @pytest.mark.asyncio
    async def test_rejects_an_empty_header(self, token):
        with pytest.raises(AuthenticationError):
            await require_scheduler_token("")

    @pytest.mark.asyncio
    async def test_rejects_a_prefix_of_the_token(self, token):
        # compare_digest must not accept a truncated match.
        with pytest.raises(AuthenticationError):
            await require_scheduler_token(token[:-1])

    @pytest.mark.asyncio
    async def test_unconfigured_disables_rather_than_opens(self, monkeypatch):
        # The dangerous failure mode: an empty secret treated as "no auth
        # needed" would leave a public endpoint that burns quota on demand.
        monkeypatch.setattr(settings, "scheduler_token", "")
        with pytest.raises(SchedulerDisabledError) as exc:
            await require_scheduler_token("anything")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unconfigured_rejects_an_empty_header_too(self, monkeypatch):
        monkeypatch.setattr(settings, "scheduler_token", "")
        with pytest.raises(SchedulerDisabledError):
            await require_scheduler_token(None)

    def test_auth_failures_are_401_not_500(self):
        assert AuthenticationError("x").status_code == 401
        assert not isinstance(AuthenticationError("x"), HTTPException)
