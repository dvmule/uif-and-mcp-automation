"""Integration tests — the v3 client against a REAL Apicurio 3.3.2 server.

Every other test in this suite mocks Apicurio with respx, using fixtures built
from the upstream OpenAPI document for tag v3.3.2. That means the client and
its tests share one premise, and if the premise is wrong both agree and both
are wrong — which is exactly what happened with the health endpoint. These
tests exist to check the premise against a running server instead.

Run from stacks/apicurio/, with RUN_INTEGRATION_TESTS=1 set:
    docker compose up -d
    ./src/.venv/bin/python -m pytest qe/tests/integration/test_registry_v3.py -v

Set REGISTRY_URL to point somewhere other than localhost:8080.
"""

import json
import os

import httpx
import pytest

from app.clients.apicurio_client import ApicurioClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS", "0") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to run",
    ),
]

_GROUP = "itest"
_ARTIFACT = "itest.master"

_V1 = json.dumps(
    {
        "type": "record",
        "name": "Master",
        "namespace": "itest",
        "fields": [{"name": "id", "type": "string"}],
    }
)
_V2 = json.dumps(
    {
        "type": "record",
        "name": "Master",
        "namespace": "itest",
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "title", "type": ["null", "string"], "default": None},
        ],
    }
)


@pytest.fixture(scope="module")
def registry(registry_url: str) -> str:
    """Skip the module unless a real registry answers on its v3 API.

    Returns the HOST base (no API path). `ApicurioClient(base_url=...)` expects
    the full API root, so callers append /apis/registry/v3 — the constructor
    overrides settings.apicurio_api_url wholesale rather than joining onto it.
    """
    try:
        resp = httpx.get(f"{registry_url}/apis/registry/v3/system/info", timeout=5)
    except httpx.HTTPError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no registry at {registry_url}: {exc}")
    if resp.status_code != 200:  # pragma: no cover - environment dependent
        pytest.skip(f"registry at {registry_url} returned {resp.status_code}")
    return registry_url


def test_server_is_v3(registry: str) -> None:
    info = httpx.get(f"{registry}/apis/registry/v3/system/info", timeout=5).json()
    assert info["version"].startswith("3."), f"expected a 3.x server, got {info}"


def test_health_is_on_the_management_port(registry: str) -> None:
    """Readiness is 9000/health/ready — NOT 8080/q/health/ready.

    This is the assertion that would have caught the broken ECS container
    health check and ALB target-group path before they were written.
    """
    base = registry.rsplit(":", 1)[0]
    assert httpx.get(f"{base}:9000/health/ready", timeout=5).status_code == 200
    assert httpx.get(f"{registry}/q/health/ready", timeout=5).status_code == 404


async def test_is_reachable_against_a_real_server(registry: str) -> None:
    async with ApicurioClient(base_url=f"{registry}/apis/registry/v3") as client:
        assert await client.is_reachable() is True


async def test_create_artifact_and_branch_latest(registry: str) -> None:
    """Covers the two shapes ADR-0012 could only verify against the spec:
    the v3 `CreateArtifact` JSON body, and `branch=latest` resolution.
    """
    async with ApicurioClient(base_url=f"{registry}/apis/registry/v3") as client:
        await client.create_artifact(
            group_id=_GROUP, artifact_id=_ARTIFACT, content=_V1, schema_type="AVRO"
        )
        # Second call must add a version rather than conflict —
        # ifExists=FIND_OR_CREATE_VERSION, v3's replacement for v2's
        # RETURN_OR_UPDATE.
        await client.create_artifact(
            group_id=_GROUP, artifact_id=_ARTIFACT, content=_V2, schema_type="AVRO"
        )

        versions = await client.list_artifact_versions(group_id=_GROUP, artifact_id=_ARTIFACT)
        assert len(versions) >= 2, versions

        latest = await client.get_artifact_content(group_id=_GROUP, artifact_id=_ARTIFACT)
        assert "title" in json.dumps(latest), "branch=latest did not return the newest version"


async def test_compatibility_rule_absent_returns_none(registry: str) -> None:
    """A freshly created artifact has no COMPATIBILITY rule; the client must
    degrade to None on the registry's 404 rather than raising."""
    async with ApicurioClient(base_url=f"{registry}/apis/registry/v3") as client:
        await client.create_artifact(
            group_id=_GROUP, artifact_id=_ARTIFACT, content=_V1, schema_type="AVRO"
        )
        assert await client.get_artifact_compatibility(
            group_id=_GROUP, artifact_id=_ARTIFACT
        ) is None


async def test_artifact_metadata_has_no_state_or_version(registry: str) -> None:
    """Documents a real v3 mapping gap rather than asserting the wrong thing.

    v3's artifact resource returns only owner/createdOn/modifiedBy/modifiedOn/
    artifactType/groupId/artifactId. `state` and `version` moved to the VERSION
    resource, but schema_service still reads them off the artifact — so the
    schema list renders state "UNKNOWN" and latest_version null for every row.
    When that is fixed, this test should be updated, not deleted.
    """
    async with ApicurioClient(base_url=f"{registry}/apis/registry/v3") as client:
        await client.create_artifact(
            group_id=_GROUP, artifact_id=_ARTIFACT, content=_V1, schema_type="AVRO"
        )
        meta = await client.get_artifact_metadata(group_id=_GROUP, artifact_id=_ARTIFACT)

    assert "state" not in meta, f"v3 now returns state on the artifact: {meta}"
    assert "version" not in meta, f"v3 now returns version on the artifact: {meta}"
