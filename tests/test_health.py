"""Health endpoint and app wiring."""

from httpx import AsyncClient


async def test_health_reports_dependency_status(client: AsyncClient) -> None:
    """Health hits the real configured database, so status depends on the
    environment. The contract is the shape, not a particular verdict."""
    response = await client.get("/health")
    body = response.json()

    assert body["version"] == "0.1.0"
    assert body["status"] in ("ok", "degraded")
    assert set(body["dependencies"]) >= {"database", "redis"}
    assert body["dependencies"]["database"] in ("ok", "error")
    assert body["dependencies"]["redis"] in ("ok", "unavailable")

    # 503 is reserved for a dead database; anything else still serves reads.
    expected = 503 if body["dependencies"]["database"] == "error" else 200
    assert response.status_code == expected


async def test_openapi_schema_builds(client: AsyncClient) -> None:
    """Catches router/schema wiring errors that only surface at spec generation."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    for expected in (
        "/health",
        "/auth/google",
        "/auth/me",
        "/companies",
        "/companies/compare",
        "/search",
        "/dashboard/stats",
        "/dashboard/trending",
        "/sources/browse",
    ):
        assert expected in paths


async def test_openapi_tags_are_documented(client: AsyncClient) -> None:
    """Every tag used by a route should have a description in the docs."""
    spec = (await client.get("/openapi.json")).json()
    documented = {tag["name"] for tag in spec.get("tags", [])}

    used = {
        tag
        for path in spec["paths"].values()
        for operation in path.values()
        for tag in operation.get("tags", [])
    }
    assert used <= documented, f"undocumented tags: {sorted(used - documented)}"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHENTICATED"
