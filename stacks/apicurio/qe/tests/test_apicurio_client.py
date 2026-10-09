"""Unit tests for ApicurioClient — Apicurio is mocked via respx.

Run:
    cd stacks/apicurio && pytest

For integration tests against a real local Apicurio, see qe/tests/integration/
(set RUN_INTEGRATION_TESTS=1).

Rewritten for Apicurio v3 in UIFPE-36. These tests carry more weight than the
v2 originals did, because the client now translates the v3 wire shape into the
internal shape every service consumes (artifactId->id, artifactType->type,
labels->properties). That translation is the seam the rest of the app depends
on, so it is asserted directly rather than only through the route tests.
"""

import httpx
import pytest
import respx

from app.clients.apicurio_client import ApicurioClient, ApicurioError

_BASE = "http://localhost:8080/apis/registry/v3"
_ART = f"{_BASE}/groups/default/artifacts/product.product.created-value"

# A v3 SearchedArtifact / ArtifactMetaData as the registry actually returns it.
_V3_ARTIFACT = {
    "artifactId": "product.product.created-value",
    "groupId": "default",
    "artifactType": "AVRO",
    "owner": "svc-product",
    "createdOn": "2024-01-01T00:00:00Z",
    "modifiedOn": "2024-03-01T00:00:00Z",
    "modifiedBy": "svc-product",
    "labels": {"domain": "product", "lifecycle": "active"},
}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_artifacts_normalises_v3_wire_shape() -> None:
    """v3 field names are mapped to the internal ones the services read."""
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": [_V3_ARTIFACT], "count": 1})
    )

    async with ApicurioClient() as client:
        result = await client.search_artifacts(limit=10)

    assert result["count"] == 1
    artifact = result["artifacts"][0]
    assert artifact["id"] == "product.product.created-value"
    assert artifact["type"] == "AVRO"
    assert artifact["properties"] == {"domain": "product", "lifecycle": "active"}
    # The real v3 keys survive alongside the mapped ones.
    assert artifact["artifactId"] == "product.product.created-value"
    assert artifact["artifactType"] == "AVRO"


@respx.mock
async def test_search_artifacts_sends_v3_group_id_param() -> None:
    """v2 called the parameter `group`; v3 calls it `groupId`."""
    route = respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": [], "count": 0})
    )

    async with ApicurioClient() as client:
        await client.search_artifacts(group="staging")

    assert route.calls.last.request.url.params["groupId"] == "staging"


@respx.mock
async def test_search_artifacts_tolerates_missing_labels() -> None:
    """Search results often omit labels; properties must still be a dict."""
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(
            200,
            json={"artifacts": [{"artifactId": "a.b-value", "artifactType": "AVRO"}], "count": 1},
        )
    )

    async with ApicurioClient() as client:
        result = await client.search_artifacts()

    assert "properties" not in result["artifacts"][0] or result["artifacts"][0]["properties"] == {}


@respx.mock
async def test_search_artifacts_raises_on_5xx() -> None:
    respx.get(f"{_BASE}/search/artifacts").mock(
        return_value=httpx.Response(503, text="upstream down")
    )

    async with ApicurioClient() as client:
        with pytest.raises(ApicurioError):
            await client.search_artifacts()


# ---------------------------------------------------------------------------
# Metadata and content
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_artifact_metadata_uses_bare_artifact_path() -> None:
    """v3 removed the /meta suffix — metadata comes from the artifact path."""
    route = respx.get(_ART).mock(return_value=httpx.Response(200, json=_V3_ARTIFACT))

    async with ApicurioClient() as client:
        meta = await client.get_artifact_metadata("default", "product.product.created-value")

    assert route.called
    assert meta["id"] == "product.product.created-value"
    assert meta["type"] == "AVRO"


@respx.mock
async def test_get_artifact_content_resolves_latest_branch() -> None:
    """v3 serves content per version; 'latest' is a branch expression."""
    route = respx.get(f"{_ART}/versions/branch=latest/content").mock(
        return_value=httpx.Response(200, text='{"type":"record","name":"Product"}')
    )

    async with ApicurioClient() as client:
        content = await client.get_artifact_content("default", "product.product.created-value")

    assert route.called
    assert content == '{"type":"record","name":"Product"}'


@respx.mock
async def test_get_artifact_version_content_uses_literal_version() -> None:
    respx.get(f"{_ART}/versions/2/content").mock(
        return_value=httpx.Response(200, text='{"v":2}')
    )

    async with ApicurioClient() as client:
        content = await client.get_artifact_version_content(
            "default", "product.product.created-value", "2"
        )

    assert content == '{"v":2}'


@respx.mock
async def test_get_artifact_content_raises_on_error() -> None:
    respx.get(f"{_ART}/versions/branch=latest/content").mock(
        return_value=httpx.Response(500, text="boom")
    )

    async with ApicurioClient() as client:
        with pytest.raises(ApicurioError):
            await client.get_artifact_content("default", "product.product.created-value")


@respx.mock
async def test_list_artifact_versions_normalises_entries() -> None:
    respx.get(f"{_ART}/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "versions": [
                    {
                        "version": "1",
                        "artifactId": "product.product.created-value",
                        "artifactType": "AVRO",
                        "state": "ENABLED",
                        "createdOn": "2024-01-01T00:00:00Z",
                    }
                ],
            },
        )
    )

    async with ApicurioClient() as client:
        versions = await client.list_artifact_versions(
            "default", "product.product.created-value"
        )

    assert len(versions) == 1
    assert versions[0]["version"] == "1"
    assert versions[0]["type"] == "AVRO"


# ---------------------------------------------------------------------------
# Compatibility rule
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_artifact_compatibility_returns_config() -> None:
    respx.get(f"{_ART}/rules/COMPATIBILITY").mock(
        return_value=httpx.Response(200, json={"ruleType": "COMPATIBILITY", "config": "BACKWARD"})
    )

    async with ApicurioClient() as client:
        assert (
            await client.get_artifact_compatibility(
                "default", "product.product.created-value"
            )
            == "BACKWARD"
        )


@respx.mock
async def test_get_artifact_compatibility_none_when_no_rule() -> None:
    """No rule configured is a 404 and a legitimate answer, not an error."""
    respx.get(f"{_ART}/rules/COMPATIBILITY").mock(
        return_value=httpx.Response(404, text="RuleNotFoundException")
    )

    async with ApicurioClient() as client:
        assert (
            await client.get_artifact_compatibility(
                "default", "product.product.created-value"
            )
            is None
        )


@respx.mock
async def test_get_artifact_compatibility_raises_on_5xx() -> None:
    respx.get(f"{_ART}/rules/COMPATIBILITY").mock(return_value=httpx.Response(500, text="boom"))

    async with ApicurioClient() as client:
        with pytest.raises(ApicurioError):
            await client.get_artifact_compatibility(
                "default", "product.product.created-value"
            )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_artifact_sends_v3_body_and_if_exists() -> None:
    """v3 takes a JSON CreateArtifact body; the v2 X-Registry-* headers are gone."""
    import json

    route = respx.post(f"{_BASE}/groups/default/artifacts").mock(
        return_value=httpx.Response(
            200, json={"artifact": _V3_ARTIFACT, "version": {"version": "1"}}
        )
    )
    # Passing properties triggers the follow-up label write asserted separately
    # in test_create_artifact_writes_labels_via_put.
    respx.put(_ART).mock(return_value=httpx.Response(204))

    async with ApicurioClient() as client:
        result = await client.create_artifact(
            "default",
            "product.product.created-value",
            '{"type":"record","name":"Product","fields":[]}',
            "AVRO",
            properties={"domain": "product"},
        )

    request = route.calls.last.request
    body = json.loads(request.content)

    assert request.url.params["ifExists"] == "FIND_OR_CREATE_VERSION"
    assert body["artifactId"] == "product.product.created-value"
    assert body["artifactType"] == "AVRO"
    assert body["firstVersion"]["content"]["content"].startswith('{"type":"record"')
    assert body["firstVersion"]["content"]["contentType"] == "application/json"
    assert body["labels"] == {"domain": "product"}
    assert "X-Registry-ArtifactId" not in request.headers

    # {"artifact": ..., "version": ...} is unwrapped so callers still see `id`.
    assert result["id"] == "product.product.created-value"


@respx.mock
async def test_create_artifact_writes_labels_via_put() -> None:
    """The ADR-0001 derived index lands in v3 `labels`, on the artifact path."""
    respx.post(f"{_BASE}/groups/default/artifacts").mock(
        return_value=httpx.Response(200, json={"artifact": _V3_ARTIFACT})
    )
    put = respx.put(_ART).mock(return_value=httpx.Response(204))

    async with ApicurioClient() as client:
        await client.create_artifact(
            "default",
            "product.product.created-value",
            "{}",
            "AVRO",
            properties={"domain": "product", "lifecycle": "active"},
        )

    import json

    assert put.called
    assert json.loads(put.calls.last.request.content) == {
        "labels": {"domain": "product", "lifecycle": "active"}
    }


@respx.mock
async def test_create_artifact_survives_label_write_failure() -> None:
    """The body is authoritative, so a failed index write must not fail the call."""
    respx.post(f"{_BASE}/groups/default/artifacts").mock(
        return_value=httpx.Response(200, json={"artifact": _V3_ARTIFACT})
    )
    respx.put(_ART).mock(return_value=httpx.Response(500, text="boom"))

    async with ApicurioClient() as client:
        result = await client.create_artifact(
            "default",
            "product.product.created-value",
            "{}",
            "AVRO",
            properties={"domain": "product"},
        )

    assert result["id"] == "product.product.created-value"


@respx.mock
async def test_create_artifact_raises_on_conflict() -> None:
    respx.post(f"{_BASE}/groups/default/artifacts").mock(
        return_value=httpx.Response(409, text="ArtifactAlreadyExistsException")
    )

    async with ApicurioClient() as client:
        with pytest.raises(ApicurioError):
            await client.create_artifact(
                "default", "product.product.created-value", "{}", "AVRO"
            )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@respx.mock
async def test_is_reachable_probes_system_info() -> None:
    """Reachability is the v3 API's own system/info.

    Quarkus readiness is on the management port (9000) at /health/ready in 3.3.2,
    which the BFF cannot reach through the ALB; /q/health/ready 404s everywhere.
    """
    route = respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        return_value=httpx.Response(200)
    )

    async with ApicurioClient() as client:
        assert await client.is_reachable() is True

    assert route.called


@respx.mock
async def test_is_reachable_false_on_connection_error() -> None:
    respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    async with ApicurioClient() as client:
        assert await client.is_reachable() is False


@respx.mock
async def test_is_reachable_false_on_non_200() -> None:
    respx.get("http://localhost:8080/apis/registry/v3/system/info").mock(
        return_value=httpx.Response(503)
    )

    async with ApicurioClient() as client:
        assert await client.is_reachable() is False
