"""Unit tests for the service portfolio endpoint.

Uses respx to mock Apicurio — no real network required.
CMDB is the in-memory MockCmdbClient (no mock needed).

Note: /search/artifacts never returns `properties` in real Apicurio v2.
Properties come from the per-artifact /meta endpoint, which list_schemas()
now calls in parallel. All tests here mock both search and /meta.
"""

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

_BASE = "http://localhost:8080/apis/registry/v3"

# Search response — no properties (mirrors real Apicurio behaviour).
_ARTIFACTS = [
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
    {
        "artifactId": "learning.learner.enrolled-value",
        "groupId": "default",
        "artifactType": "AVRO",
        "state": "ENABLED",
    },
]

_SEARCH_RESPONSE = {"artifacts": _ARTIFACTS, "count": len(_ARTIFACTS)}

# Per-artifact /meta responses — properties live here.
_META = {
    "product.product.created-value": {
        "artifactId": "product.product.created-value", "groupId": "default",
        "artifactType": "AVRO", "state": "ENABLED",
        "labels": {"owner.service": "CI0093421", "domain": "product",
                       "dataClassification": "internal", "lifecycle": "active"},
    },
    "content.asset.published-value": {
        "artifactId": "content.asset.published-value", "groupId": "default",
        "artifactType": "AVRO", "state": "ENABLED",
        "labels": {"owner.service": "CI0099999", "domain": "content",
                       "dataClassification": "internal", "lifecycle": "active"},
    },
    "learning.learner.enrolled-value": {
        "artifactId": "learning.learner.enrolled-value", "groupId": "default",
        "artifactType": "AVRO", "state": "ENABLED",
        "labels": {"owner.service": "CI0093422", "domain": "learning",
                       "dataClassification": "internal", "lifecycle": "active"},
    },
}


def _mock_apicurio() -> None:
    """Register search + per-artifact /meta mocks for all three test artifacts."""
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json=_SEARCH_RESPONSE)
    )
    for artifact_id, meta in _META.items():
        respx.get(f"{_BASE}/groups/default/artifacts/{artifact_id}").mock(
            return_value=httpx.Response(200, json=meta)
        )


@respx.mock
def test_get_portfolio_known_urn_returns_200() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ci_id"] == "CI0093421"
    assert body["urn"] == "urn:pearson:service:product:catalog-svc"
    assert body["name"] == "catalog-svc"


@respx.mock
def test_get_portfolio_unknown_urn_returns_404() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aunknown%3Anobody/schema-portfolio"
    )
    assert resp.status_code == 404


@respx.mock
def test_produces_filtered_by_cmdb_manifest() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    body = resp.json()
    produces_ids = [s["artifact_id"] for s in body["produces"]]
    # product.product.created-value is in catalog-svc CMDB manifest produces
    assert "product.product.created-value" in produces_ids
    # learning.learner.enrolled-value is not in catalog-svc CMDB manifest
    assert "learning.learner.enrolled-value" not in produces_ids


@respx.mock
def test_consumes_filtered_by_cmdb_manifest() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    body = resp.json()
    consumes_ids = [s["artifact_id"] for s in body["consumes"]]
    # catalog-svc.consumes = ["content.asset.published-value", ...] per CMDB fixture
    assert "content.asset.published-value" in consumes_ids
    assert "product.product.created-value" not in consumes_ids


@respx.mock
def test_portfolio_includes_dependencies() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "dependencies" in body
    assert "upstream" in body["dependencies"]
    assert "downstream" in body["dependencies"]


@respx.mock
def test_portfolio_downstream_contains_consumers_of_produced_schemas() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    body = resp.json()
    downstream_names = {s["name"] for s in body["dependencies"]["downstream"]}
    # enrollment-svc consumes product.product.created-value which catalog-svc produces
    assert "enrollment-svc" in downstream_names
    # catalog-svc should not appear as its own downstream
    assert "catalog-svc" not in downstream_names


@respx.mock
def test_portfolio_upstream_contains_producers_of_consumed_schemas() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    body = resp.json()
    upstream_names = {s["name"] for s in body["dependencies"]["upstream"]}
    # catalog-svc consumes content.asset.published-value, produced by content-svc
    assert "content-svc" in upstream_names


@respx.mock
def test_dependencies_exclude_unregistered_topics() -> None:
    """Dependency edges must be backed by a registered schema.

    catalog-svc declares 7 produce topics in CMDB but only product.product.created-value
    is registered in the mocked Apicurio. course-svc consumes both
    product.product.created-value (registered) AND product.bundle.published-value
    (unregistered). shared_schema_count must be 1, not 2.
    """
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    body = resp.json()
    downstream = {s["name"]: s for s in body["dependencies"]["downstream"]}
    assert "course-svc" in downstream
    # only 1 registered shared schema — product.bundle.published-value is not in mocked Apicurio
    assert downstream["course-svc"]["shared_schema_count"] == 1


def test_list_services_returns_200() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


def test_list_services_shape() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    body = resp.json()
    first = body[0]
    assert "ci_id" in first
    assert "urn" in first
    assert "name" in first
    assert "domain" in first
    assert "produces_count" in first
    assert "consumes_count" in first
    assert "produces" in first
    assert "consumes" in first
    assert isinstance(first["produces"], list)
    assert isinstance(first["consumes"], list)


def test_list_services_topic_arrays_match_cmdb_manifest() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    body = resp.json()
    catalog = next(
        (s for s in body if s["name"] == "catalog-svc" and s["domain"] == "product"), None
    )
    assert catalog is not None
    assert "product.product-value" in catalog["produces"]
    assert "content.asset.published-value" in catalog["consumes"]
    assert len(catalog["produces"]) == catalog["produces_count"]
    assert len(catalog["consumes"]) == catalog["consumes_count"]


def test_list_services_counts_match_cmdb_manifest() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    body = resp.json()
    catalog = next(
        (s for s in body if s["name"] == "catalog-svc" and s["domain"] == "product"), None
    )
    assert catalog is not None
    # catalog-svc.produces has 17 entries: the 10 coordinates registered on
    # 2026-09-25 (mapping, gold, and the api/event/message created+updated
    # pairs, plus marketing-copy's two) and the 7 original demo subjects,
    # which are retained because this module's scenario depends on them.
    # Stated as a sum so the next person to change the manifest knows what
    # the number is made of rather than adjusting a bare literal.
    assert catalog["produces_count"] == 10 + 7
    # catalog-svc.consumes has 3 entries
    assert catalog["consumes_count"] == 3


def test_cmdb_find_producers_of() -> None:
    """MockCmdbClient.find_producers_of returns CIs whose produces list contains the artifact."""
    import asyncio

    from app.clients.cmdb_client import MockCmdbClient
    cmdb = MockCmdbClient()
    # content.asset.published-value is produced by content-svc (CI0093430 and CI0093443)
    producers = asyncio.run(cmdb.find_producers_of("content.asset.published-value"))
    producer_names = {ci.name for ci in producers}
    assert "content-svc" in producer_names


def test_cmdb_find_producers_of_no_match() -> None:
    import asyncio

    from app.clients.cmdb_client import MockCmdbClient
    cmdb = MockCmdbClient()
    result = asyncio.run(cmdb.find_producers_of("nonexistent.subject-value"))
    assert result == []


def test_list_services_includes_app_fields() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    body = resp.json()
    catalog = next(
        (s for s in body if s["name"] == "catalog-svc" and s["domain"] == "product"), None
    )
    assert catalog is not None
    assert "app_id" in catalog
    assert "app_name" in catalog
    assert catalog["app_id"] == "SVC01328"
    assert catalog["app_name"] == "GIP - Global Integration Platform"


def test_list_services_enrollment_svc_app() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    body = resp.json()
    enrollment = next((s for s in body if s["name"] == "enrollment-svc"), None)
    assert enrollment is not None
    assert enrollment["app_id"] == "SVC04622"
    assert "BigCommerce" in (enrollment["app_name"] or "")


@respx.mock
def test_portfolio_includes_app_fields() -> None:
    _mock_apicurio()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/services/urn%3Apearson%3Aservice%3Aproduct%3Acatalog-svc/schema-portfolio"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("app_id") == "SVC01328"
    assert body.get("app_name") == "GIP - Global Integration Platform"
