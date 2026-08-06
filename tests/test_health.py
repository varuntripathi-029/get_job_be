"""Health endpoint and app wiring."""

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


async def test_openapi_schema_builds(client: AsyncClient) -> None:
    """Catches router/schema wiring errors that only surface at spec generation."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    for expected in ("/health", "/auth/google", "/auth/me", "/companies"):
        assert expected in paths


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"
