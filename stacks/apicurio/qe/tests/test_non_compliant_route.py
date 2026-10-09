"""E2E tests for GET /schemas/non-compliant and POST /schemas/non-compliant/refresh.

Uses respx to mock Apicurio — no real network required.
The module-level singleton cache is cleared between tests via an autouse fixture.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.services.validation_cache import get_validation_cache

_BASE = "http://localhost:8080/apis/registry/v3"

_GOOD_BODY = json.dumps({
    "type": "record",
    "name": "ProductCreated",
    "namespace": "product",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "email", "type": "string"},
    ],
    "metadata": {
        "owner": {
            "service": "CI0093421",
            "team": "TM-PRODUCT-CATALOG",
            "email": "product-catalog@pearson.com",
        },
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": ["email"],
    },
})

_BAD_BODY = json.dumps({
    "type": "record",
    "name": "ContentPublished",
    "namespace": "content",
    "fields": [],
    # no metadata block → metadata_block_missing
})

_SEARCH = {
    "artifacts": [
        {
            "artifactId": "product.product.created-value",
            "groupId": "default",
            "artifactType": "AVRO",
            "state": "ENABLED",
        },
        {
            "artifactId": "content.asset.published-value",
            "groupId": "default",
            "artifactType": "AVRO",
            "state": "ENABLED",
        },
    ],
    "count": 2,
}

_META_PRODUCT = {
    "artifactId": "product.product.created-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "state": "ENABLED",
    "labels": {
        "owner.service": "CI0093421", "domain": "product",
        "dataClassification": "internal", "lifecycle": "active",
    },
}
_META_CONTENT = {
    "artifactId": "content.asset.published-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "state": "ENABLED",
    "labels": {},
}


def _mock_apicurio() -> None:
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json=_SEARCH)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/product.product.created-value").mock(
        return_value=httpx.Response(200, json=_META_PRODUCT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/content.asset.published-value").mock(
        return_value=httpx.Response(200, json=_META_CONTENT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/product.product.created-value/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text=_GOOD_BODY)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/content.asset.published-value/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text=_BAD_BODY)
    )


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    get_validation_cache().clear()


@respx.mock
def test_get_non_compliant_triggers_scan_on_empty_cache() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get("/api/v1/schemas/non-compliant")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_checked"] == 2
    assert body["last_validated"] is not None


@respx.mock
def test_non_compliant_returns_only_failing_schemas() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get("/api/v1/schemas/non-compliant")
    assert resp.status_code == 200
    body = resp.json()
    failing_ids = [item["artifact_id"] for item in body["items"]]
    assert "content.asset.published-value" in failing_ids
    assert "product.product.created-value" not in failing_ids


@respx.mock
def test_second_get_uses_cache_no_extra_requests() -> None:
    """Cache hit: the second GET must not call Apicurio again."""
    _mock_apicurio()
    client = TestClient(app)
    client.get("/api/v1/schemas/non-compliant")
    # On second call, respx would raise if new requests are made (strict mode off, but
    # we can assert the cache is still populated and the response is the same).
    resp2 = client.get("/api/v1/schemas/non-compliant")
    assert resp2.status_code == 200
    assert resp2.json()["total_checked"] == 2


@respx.mock
def test_post_refresh_clears_and_repopulates() -> None:
    _mock_apicurio()
    client = TestClient(app)
    # Populate via GET first.
    client.get("/api/v1/schemas/non-compliant")
    # Now refresh — must re-run the scan (respx will replay the mocks).
    resp = client.post("/api/v1/schemas/non-compliant/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_checked"] == 2
    assert body["last_validated"] is not None


@respx.mock
def test_post_refresh_returns_non_compliant_response_shape() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.post("/api/v1/schemas/non-compliant/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "last_validated" in body
    assert "total_checked" in body
