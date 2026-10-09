"""Route tests for POST /api/v1/services/onboard."""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import get_settings
from app.main import app

_APICURIO = "http://localhost:8080/apis/registry/v3"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock
def test_onboard_returns_200():
    respx.get(f"{_APICURIO}/groups/default/artifacts/learning.learner.enrolled-value").mock(
        return_value=Response(
            200,
            json={"artifactType": "AVRO", "artifactId": "learning.learner.enrolled-value"},
        )
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/services/onboard",
        json={
            "service_urn": "urn:pearson:service:learning:enrollment-svc",
            "produces": ["learning.learner.enrolled-value"],
            "consumes": [],
        },
    )
    assert resp.status_code == 200


@respx.mock
def test_onboard_known_urn_resolves_identity():
    respx.get(f"{_APICURIO}/groups/default/artifacts/learning.learner.enrolled-value").mock(
        return_value=Response(200, json={"artifactType": "AVRO"})
    )

    client = TestClient(app)
    body = client.post(
        "/api/v1/services/onboard",
        json={
            "service_urn": "urn:pearson:service:learning:enrollment-svc",
            "produces": ["learning.learner.enrolled-value"],
            "consumes": [],
        },
    ).json()

    assert body["ci_id"] == "CI0093422"
    assert body["name"] == "enrollment-svc"


@respx.mock
def test_onboard_unregistered_produced_topic_flags_d6():
    respx.get(f"{_APICURIO}/groups/default/artifacts/learning.ghost-value").mock(
        return_value=Response(404, json={"message": "Not found"})
    )

    client = TestClient(app)
    body = client.post(
        "/api/v1/services/onboard",
        json={
            "service_urn": "urn:pearson:service:learning:enrollment-svc",
            "produces": ["learning.ghost-value"],
            "consumes": [],
        },
    ).json()

    assert body["produces"][0]["registered"] is False
    assert body["produces"][0]["drift"] == "D6"


@respx.mock
def test_onboard_unknown_urn_no_graph_update():
    respx.get(f"{_APICURIO}/groups/default/artifacts/some.topic-value").mock(
        return_value=Response(200, json={"artifactType": "AVRO"})
    )

    client = TestClient(app)
    body = client.post(
        "/api/v1/services/onboard",
        json={
            "service_urn": "urn:pearson:service:unknown:ghost-svc",
            "produces": ["some.topic-value"],
            "consumes": [],
        },
    ).json()

    assert body["ci_id"] is None


@respx.mock
def test_onboard_response_shape():
    respx.get(f"{_APICURIO}/groups/default/artifacts/product.product-value").mock(
        return_value=Response(200, json={"artifactType": "AVRO"})
    )
    respx.get(f"{_APICURIO}/groups/default/artifacts/finance.pricing.changed-value").mock(
        return_value=Response(200, json={"artifactType": "AVRO"})
    )

    client = TestClient(app)
    body = client.post(
        "/api/v1/services/onboard",
        json={
            "service_urn": "urn:pearson:service:finance:pricing-svc",
            "produces": ["finance.pricing.changed-value"],
            "consumes": ["product.product-value"],
        },
    ).json()

    assert "service_urn" in body
    assert "ci_id" in body
    assert "produces" in body
    assert "consumes" in body
    assert "graph_updated" not in body  # removed with the RDF graph (ADR-0007)
    assert body["produces"][0]["topic"] == "finance.pricing.changed-value"
    assert body["consumes"][0]["topic"] == "product.product-value"
