"""Request validation, error shape, and rate limiting on the public API."""

import pytest

from app.common.rate_limit import _limiter


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Buckets are process-global; without this, tests leak into each other."""
    _limiter.reset()
    yield
    _limiter.reset()


# --- Search ------------------------------------------------------------------


async def test_search_requires_a_query(stub_client) -> None:
    response = await stub_client.get("/search")
    assert response.status_code == 422


async def test_search_rejects_a_one_character_query(stub_client) -> None:
    """Single characters match nearly everything and are never a real search."""
    response = await stub_client.get("/search", params={"q": "a"})
    assert response.status_code == 422


async def test_search_rejects_an_unknown_type(stub_client) -> None:
    response = await stub_client.get(
        "/search", params={"q": "razorpay", "type": "pony"}
    )
    assert response.status_code == 422


# --- Comparison --------------------------------------------------------------


async def test_compare_requires_at_least_two_companies(stub_client) -> None:
    response = await stub_client.get("/companies/compare", params={"slugs": "razorpay"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "VALIDATION_ERROR"
    assert "at least 2" in body["message"]


async def test_compare_rejects_more_than_five(stub_client) -> None:
    response = await stub_client.get(
        "/companies/compare", params={"slugs": "a,b,c,d,e,f"}
    )
    assert response.status_code == 422
    assert "at most 5" in response.json()["message"]


async def test_compare_deduplicates_repeated_slugs(stub_client) -> None:
    """?slugs=a,a is one company, so it fails the two-company minimum."""
    response = await stub_client.get(
        "/companies/compare", params={"slugs": "acme,acme"}
    )
    assert response.status_code == 422
    assert "at least 2" in response.json()["message"]


async def test_compare_route_is_not_shadowed_by_the_slug_route(stub_client) -> None:
    """/compare must be matched as its own route, not as a company slug.

    A 404 "No company with slug 'compare'" would mean the /{slug} route won.
    """
    response = await stub_client.get("/companies/compare", params={"slugs": "one"})
    assert response.status_code == 422
    assert "compare" not in response.json()["message"].lower()


# --- Error shape -------------------------------------------------------------


async def test_app_errors_use_the_flat_shape(stub_client) -> None:
    response = await stub_client.get("/companies/compare", params={"slugs": "only-one"})
    body = response.json()
    assert set(body) == {"error", "message"}
    assert isinstance(body["error"], str)
    assert isinstance(body["message"], str)


# --- Rate limiting -----------------------------------------------------------


async def test_auth_endpoint_blocks_the_eleventh_attempt(stub_client) -> None:
    """10/minute on sign-in, so credential stuffing costs something."""
    codes = []
    for _ in range(11):
        response = await stub_client.post(
            "/auth/google", json={"credential": "not-a-real-token"}
        )
        codes.append(response.status_code)

    assert 429 not in codes[:10], f"limiter fired early: {codes}"
    assert codes[10] == 429
    assert response.json()["error"] == "RATE_LIMITED"


async def test_search_allows_thirty_per_minute(stub_client) -> None:
    codes = []
    for _ in range(31):
        response = await stub_client.get("/search", params={"q": "xx", "type": "pony"})
        codes.append(response.status_code)
    # The 422s still consume budget — the limit runs before validation.
    assert codes[30] == 429


def test_limiter_isolates_keys() -> None:
    for _ in range(10):
        assert _limiter.check("auth:1.1.1.1", 10, 60)
    assert _limiter.check("auth:1.1.1.1", 10, 60) is False
    # A different IP has its own budget.
    assert _limiter.check("auth:2.2.2.2", 10, 60) is True


def test_limiter_scopes_are_independent() -> None:
    for _ in range(10):
        _limiter.check("auth:1.1.1.1", 10, 60)
    assert _limiter.check("auth:1.1.1.1", 10, 60) is False
    assert _limiter.check("search:1.1.1.1", 30, 60) is True
