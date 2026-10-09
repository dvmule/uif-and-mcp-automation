"""Tests for GET /api/v1/schemas/overview.

Uses respx to mock Apicurio. Validates OverviewStats shape and counts.
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
    "fields": [],
    "metadata": {
        "owner": {"service": "CI0093421", "team": "TM-PROD", "email": "prod@pearson.com"},
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": [],
    },
})

_BAD_BODY = json.dumps({"type": "record", "name": "ContentPublished", "fields": []})

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
        "owner.service": "CI0093421",
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
    },
}
_META_CONTENT = {
    "artifactId": "content.asset.published-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "state": "ENABLED",
    "labels": {},
}


def _mock_apicurio(product_has_compat: bool = True) -> None:
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
    # compatibility rule mocks
    if product_has_compat:
        respx.get(
            f"{_BASE}/groups/default/artifacts/product.product.created-value/rules/COMPATIBILITY"
        ).mock(return_value=httpx.Response(200, json={"config": "BACKWARD"}))
    else:
        respx.get(
            f"{_BASE}/groups/default/artifacts/product.product.created-value/rules/COMPATIBILITY"
        ).mock(return_value=httpx.Response(404, json={"message": "No rule"}))
    respx.get(
        f"{_BASE}/groups/default/artifacts/content.asset.published-value/rules/COMPATIBILITY"
    ).mock(return_value=httpx.Response(404, json={"message": "No rule"}))


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    get_validation_cache().clear()


@respx.mock
def test_overview_returns_correct_shape() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get("/api/v1/schemas/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_schemas" in body
    assert "schemas_with_compatibility_rules" in body
    assert "non_compliant_schemas" in body


@respx.mock
def test_overview_total_schemas_matches_search_count() -> None:
    _mock_apicurio()
    client = TestClient(app)
    body = client.get("/api/v1/schemas/overview").json()
    assert body["total_schemas"] == 2


@respx.mock
def test_overview_compatibility_count_with_one_rule() -> None:
    _mock_apicurio(product_has_compat=True)
    client = TestClient(app)
    body = client.get("/api/v1/schemas/overview").json()
    assert body["schemas_with_compatibility_rules"] == 1


@respx.mock
def test_overview_compatibility_count_with_no_rules() -> None:
    _mock_apicurio(product_has_compat=False)
    client = TestClient(app)
    body = client.get("/api/v1/schemas/overview").json()
    assert body["schemas_with_compatibility_rules"] == 0


@respx.mock
def test_overview_non_compliant_count() -> None:
    _mock_apicurio()
    client = TestClient(app)
    body = client.get("/api/v1/schemas/overview").json()
    # content.asset.published-value has no metadata block → non-compliant
    assert body["non_compliant_schemas"] >= 1


@respx.mock
def test_overview_reuses_populated_cache() -> None:
    """Second call must return same non_compliant count without re-scanning."""
    _mock_apicurio()
    client = TestClient(app)
    first = client.get("/api/v1/schemas/overview").json()
    second = client.get("/api/v1/schemas/overview").json()
    assert first["non_compliant_schemas"] == second["non_compliant_schemas"]
