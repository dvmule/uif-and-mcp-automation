"""Smoke test for the health endpoint.

Apicurio is the only downstream dependency since the Oxigraph removal
(ADR-0007), so `degraded` now has exactly one cause.
"""

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app


@respx.mock
def test_health_apicurio_up() -> None:
    respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        return_value=httpx.Response(200)
    )

    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "up"
    dep_names = {d["name"]: d["state"] for d in body["dependencies"]}
    assert dep_names["apicurio"] == "up"


@respx.mock
def test_health_apicurio_down() -> None:
    respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "degraded"
    dep_names = {d["name"]: d["state"] for d in body["dependencies"]}
    assert dep_names["apicurio"] == "down"


@respx.mock
def test_health_reports_only_apicurio() -> None:
    """Guards against a dependency being reintroduced without a test."""
    respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        return_value=httpx.Response(200)
    )

    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert [d["name"] for d in response.json()["dependencies"]] == ["apicurio"]
