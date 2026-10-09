"""Integration tests — BFF connectivity to Apicurio.

Mirrors the curl smoke-checks run after deployment:
  1. GET /api/v1/health   — BFF up, Apicurio reachable
  2. GET /api/v1/schemas  — schema list from Apicurio

The SPARQL-passthrough and KG-graph checks were removed with the RDF graph
(ADR-0007).

Run with:
    BFF_URL=http://... RUN_INTEGRATION_TESTS=1 pytest tests/integration/ -v
"""

import httpx
import pytest

# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_200(self, client: httpx.Client) -> None:
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_state_is_up(self, client: httpx.Client) -> None:
        body = client.get("/api/v1/health").json()
        assert body["state"] == "up", f"BFF state is not up: {body}"

    def test_apicurio_dependency_is_up(self, client: httpx.Client) -> None:
        deps = client.get("/api/v1/health").json()["dependencies"]
        apicurio = next((d for d in deps if d["name"] == "apicurio"), None)
        assert apicurio is not None, "apicurio dependency missing from health response"
        assert apicurio["state"] == "up", f"Apicurio is not reachable: {apicurio}"


# ── Schema list ────────────────────────────────────────────────────────────────

class TestSchemaList:
    # NO trailing slash, and that is now the contract rather than a preference.
    # schemas.py:32 declares the collection `@router.get("")`, matching
    # services.py:32, so the registered path is `/api/v1/schemas`.
    #
    # It was `@router.get("/")` until this commit, which registered
    # `/api/v1/schemas/` instead and made the slash load-bearing -- the only
    # bare-slash route in the API. This test asked without the slash, got a 307
    # from Starlette's redirect_slashes, and since the session client does not
    # follow redirects (httpx defaults follow_redirects=False,
    # qe/tests/integration/conftest.py:70) it read as a broken deploy. The
    # redirect is gone now; a 307 here would mean the route regressed to `"/"`.
    def test_returns_200(self, client: httpx.Client) -> None:
        r = client.get("/api/v1/schemas")
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}. A 307 means the collection is "
            'registered as `/api/v1/schemas/` again -- check schemas.py still '
            'declares `@router.get("")` and not `@router.get("/")`.'
        )

    # Seeded-data assumptions, split out per DEPLOY-DEV.md Phase 5. A brand-new
    # registry is empty, so without this marker tier 2 fails against exactly the
    # fresh deploy it exists to verify -- and reads as a broken deploy rather
    # than an empty one.
    @pytest.mark.requires_seed
    def test_returns_non_empty_list(self, client: httpx.Client) -> None:
        body = client.get("/api/v1/schemas").json()
        assert isinstance(body, list), f"Expected list, got {type(body)}"
        assert len(body) > 0, "Schema list is empty — Apicurio has no artifacts"

    @pytest.mark.requires_seed
    def test_items_have_required_fields(self, client: httpx.Client) -> None:
        items = client.get("/api/v1/schemas").json()
        required = {"artifact_id", "group_id", "schema_type"}
        for item in items:
            missing = required - item.keys()
            assert not missing, f"Schema item missing fields {missing}: {item}"
