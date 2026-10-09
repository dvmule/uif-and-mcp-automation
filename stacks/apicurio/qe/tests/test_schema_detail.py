"""Unit tests for the schema detail endpoint.

Uses respx to mock all Apicurio calls — no real network required.
"""

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

_BASE = "http://localhost:8080/apis/registry/v3"

_META = {
    "artifactId": "commerce.product-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "state": "ENABLED",
    "version": "2",
    "createdOn": "2024-01-01T00:00:00Z",
    "modifiedOn": "2024-03-01T00:00:00Z",
    "labels": {
        "owner.service": "CI0093421",
        "owner.team": "TM-COMMERCE",
        "owner.email": "commerce@pearson.com",
        "domain": "commerce",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": '["$.customerId"]',
    },
}

_CONTENT = '{"type":"record","name":"Product","fields":[]}'

_VERSIONS = {
    "count": 2,
    "versions": [
        {"version": "1", "state": "ENABLED", "createdOn": "2024-01-01T00:00:00Z"},
        {"version": "2", "state": "ENABLED", "createdOn": "2024-03-01T00:00:00Z"},
    ],
}

_COMPAT = {"ruleType": "COMPATIBILITY", "config": "BACKWARD"}


@respx.mock
def test_get_schema_detail_returns_full_payload() -> None:
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value").mock(
        return_value=httpx.Response(200, json=_META)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text=_CONTENT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions").mock(
        return_value=httpx.Response(200, json=_VERSIONS)
    )
    respx.get(
        f"{_BASE}/groups/default/artifacts/commerce.product-value/rules/COMPATIBILITY"
    ).mock(return_value=httpx.Response(200, json=_COMPAT))

    client = TestClient(app)
    resp = client.get("/api/v1/schemas/default/commerce.product-value")

    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == "commerce.product-value"
    assert body["content"] == _CONTENT
    assert body["compatibility"] == "BACKWARD"
    assert len(body["versions"]) == 2
    assert body["owner"]["service"] == "CI0093421"
    assert body["classification"] == "internal"
    assert body["pii_fields"] == ["$.customerId"]


@respx.mock
def test_get_schema_detail_no_compat_rule() -> None:
    """compatibility is None when Apicurio returns 404 for the rule."""
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value").mock(
        return_value=httpx.Response(200, json=_META)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text=_CONTENT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions").mock(
        return_value=httpx.Response(200, json=_VERSIONS)
    )
    respx.get(
        f"{_BASE}/groups/default/artifacts/commerce.product-value/rules/COMPATIBILITY"
    ).mock(return_value=httpx.Response(404, json={"message": "No rule"}))

    client = TestClient(app)
    resp = client.get("/api/v1/schemas/default/commerce.product-value")

    assert resp.status_code == 200
    assert resp.json()["compatibility"] is None


@respx.mock
def test_get_schema_detail_specific_version() -> None:
    """?version=1 fetches version-specific content."""
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value").mock(
        return_value=httpx.Response(200, json=_META)
    )
    respx.get(
        f"{_BASE}/groups/default/artifacts/commerce.product-value/versions/1/content"
    ).mock(return_value=httpx.Response(200, text='{"version":"one"}'))
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions").mock(
        return_value=httpx.Response(200, json=_VERSIONS)
    )
    respx.get(
        f"{_BASE}/groups/default/artifacts/commerce.product-value/rules/COMPATIBILITY"
    ).mock(return_value=httpx.Response(200, json=_COMPAT))

    client = TestClient(app)
    resp = client.get("/api/v1/schemas/default/commerce.product-value?version=1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == '{"version":"one"}'
    assert body["content_version"] == "1"


@respx.mock
def test_get_schema_detail_apicurio_down_returns_502() -> None:
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    # other endpoints won't be reached but must be registered to avoid respx errors
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text=_CONTENT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/commerce.product-value/versions").mock(
        return_value=httpx.Response(200, json=_VERSIONS)
    )
    respx.get(
        f"{_BASE}/groups/default/artifacts/commerce.product-value/rules/COMPATIBILITY"
    ).mock(return_value=httpx.Response(200, json=_COMPAT))

    client = TestClient(app)
    resp = client.get("/api/v1/schemas/default/commerce.product-value")

    assert resp.status_code == 502
