"""Unit tests proving that list_schemas() hydrates properties from /meta.

Apicurio v2 /search/artifacts omits properties. These tests verify that the
service layer fans out to /meta and merges the result so that owner, classification,
domain, and lifecycle are populated in the SchemaSummary items.
"""

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

_BASE = "http://localhost:8080/apis/registry/v3"

_SEARCH = {
    "artifacts": [
        {
            "artifactId": "product.product.created-value",
            "groupId": "default",
            "artifactType": "AVRO",
            "state": "ENABLED",
            # No 'properties' key — matches real Apicurio search behaviour.
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
        "owner.service": "CI0093421",
        "owner.team": "TM-PRODUCT-CATALOG",
        "owner.email": "product-catalog@pearson.com",
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": "[]",
    },
}

_META_CONTENT = {
    "artifactId": "content.asset.published-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "state": "ENABLED",
    "labels": {
        "owner.service": "CI0093430",
        "owner.team": "TM-CONTENT-PLATFORM",
        "owner.email": "content-platform@pearson.com",
        "domain": "content",
        "dataClassification": "confidential",
        "lifecycle": "active",
        "piiFields": "[]",
    },
}


@respx.mock
def test_list_schemas_hydrates_properties_from_meta() -> None:
    """Properties absent from search are fetched from /meta and appear in the response."""
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json=_SEARCH)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/product.product.created-value").mock(
        return_value=httpx.Response(200, json=_META_PRODUCT)
    )
    respx.get(f"{_BASE}/groups/default/artifacts/content.asset.published-value").mock(
        return_value=httpx.Response(200, json=_META_CONTENT)
    )

    client = TestClient(app)
    resp = client.get("/api/v1/schemas")

    assert resp.status_code == 200
    items = {s["artifact_id"]: s for s in resp.json()["items"]}

    assert items["product.product.created-value"]["owner"]["service"] == "CI0093421"
    assert items["product.product.created-value"]["classification"] == "internal"
    assert items["product.product.created-value"]["domain"] == "product"

    assert items["content.asset.published-value"]["owner"]["service"] == "CI0093430"
    assert items["content.asset.published-value"]["classification"] == "confidential"
    assert items["content.asset.published-value"]["domain"] == "content"


@respx.mock
def test_list_schemas_tolerates_meta_failure() -> None:
    """A single /meta failure is swallowed — the artifact still appears with empty metadata."""
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json=_SEARCH)
    )
    # product meta succeeds
    respx.get(f"{_BASE}/groups/default/artifacts/product.product.created-value").mock(
        return_value=httpx.Response(200, json=_META_PRODUCT)
    )
    # content meta fails
    respx.get(f"{_BASE}/groups/default/artifacts/content.asset.published-value").mock(
        return_value=httpx.Response(503, text="upstream down")
    )

    client = TestClient(app)
    resp = client.get("/api/v1/schemas")

    assert resp.status_code == 200
    items = {s["artifact_id"]: s for s in resp.json()["items"]}

    # product still populated
    assert items["product.product.created-value"]["owner"]["service"] == "CI0093421"
    # content artifact still present but metadata is empty
    assert items["content.asset.published-value"]["owner"]["service"] is None
    assert items["content.asset.published-value"]["classification"] is None
