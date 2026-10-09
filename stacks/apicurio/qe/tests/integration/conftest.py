"""Shared fixtures for integration tests.

These tests hit a live BFF deployment. Set the following env vars before running:

    BFF_URL              Base URL of the deployed BFF (e.g. http://internal-...:8000)
                         or, from CI, the PUBLIC API Gateway endpoint -- that is
                         the only one a GitHub-hosted runner can reach, since the
                         BFF's own ALB is internal.
    RUN_INTEGRATION_TESTS=1   Guard flag — tests are skipped unless this is set
    APICURIO_CI_KEY      Shared secret for the API Gateway authorizer, sent as a
                         request header. Needed whenever BFF_URL is the API
                         Gateway and the caller's source IP is not allowlisted,
                         which is always true of a CI runner -- runner addresses
                         move, so the header is the only workable credential
                         path for CI (DEPLOY-DEV.md Phase 5). Unset is fine when
                         talking to a BFF directly, e.g. docker-compose.
    APICURIO_CI_HEADER   Header name for that secret. Defaults to X-UIF-CI-Key;
                         the authoritative value is `terraform output
                         authorizer_ci_header`.
    UI_ORIGIN            The deployed UI's CloudFront origin, for the CORS
                         preflight check. Skipped when unset.
"""

import os

import httpx
import pytest

_RUN = os.getenv("RUN_INTEGRATION_TESTS", "0") == "1"
pytestmark = pytest.mark.skipif(not _RUN, reason="set RUN_INTEGRATION_TESTS=1 to run")


@pytest.fixture(scope="session")
def bff_url() -> str:
    url = os.environ.get("BFF_URL", "").rstrip("/")
    if not url:
        pytest.skip("BFF_URL not set")
    return url



@pytest.fixture(scope="session")
def registry_url() -> str:
    """Base URL of a live Apicurio Registry (no API path).

    Defaults to the docker-compose instance at ../../docker-compose.yml, which
    is the cheapest way to satisfy this: `docker compose up -d` in
    stacks/apicurio/.
    """
    return os.environ.get("REGISTRY_URL", "http://localhost:8080").rstrip("/")


@pytest.fixture(scope="session")
def ci_headers() -> dict[str, str]:
    """The authorizer credential, when one is configured.

    Empty when APICURIO_CI_KEY is unset, which is correct for a direct BFF or a
    docker-compose target: those have no authorizer in front of them, and
    sending a stray header to them is harmless but pointless.
    """
    key = os.environ.get("APICURIO_CI_KEY", "")
    if not key:
        return {}
    return {os.environ.get("APICURIO_CI_HEADER", "X-UIF-CI-Key"): key}


@pytest.fixture(scope="session")
def client(bff_url: str, ci_headers: dict[str, str]) -> httpx.Client:
    """Session-scoped httpx client — one connection pool for all integration tests."""
    with httpx.Client(base_url=bff_url, timeout=15, headers=ci_headers) as c:
        yield c


@pytest.fixture(scope="session")
def unauthenticated_client(bff_url: str) -> httpx.Client:
    """Deliberately credential-free, for asserting the authorizer denies."""
    with httpx.Client(base_url=bff_url, timeout=15) as c:
        yield c
