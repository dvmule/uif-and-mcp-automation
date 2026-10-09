"""Integration tests — the public API Gateway in front of the BFF.

What these cover that nothing else does: the two things the public edge adds,
which are invisible when you talk to the BFF directly (docker-compose, or the
internal ALB). DEPLOY-DEV.md Phase 5 asks for both.

  1. The authorizer actually denies an uncredentialled caller.
  2. A CORS preflight from the real CloudFront origin is allowed — the failure
     mode being a UI that loads and then shows an empty list with a console
     CORS error, because the bundle was built before the API Gateway existed.

Run with:
    BFF_URL=https://<api-id>.execute-api.<region>.amazonaws.com \\
    APICURIO_CI_KEY=... UI_ORIGIN=https://<dist>.cloudfront.net \\
    RUN_INTEGRATION_TESTS=1 pytest qe/tests/integration/ -v
"""

import os

import httpx
import pytest

# ── Authorizer ────────────────────────────────────────────────────────────────


class TestAuthorizer:
    # Guarded, because "denied" is only the correct answer for SOME callers.
    #
    # The authorizer has two credential paths: an allowlisted source IP, or the
    # shared-secret header. A developer running this from an allowlisted office
    # or VPN address is SUPPOSED to be allowed with no header at all, so
    # asserting a denial there would fail for the right behaviour. CI is never
    # allowlisted -- runner addresses move, which is the whole reason the header
    # path exists -- so the smoke job sets this flag and a laptop does not.
    @pytest.mark.skipif(
        os.getenv("EXPECT_AUTHORIZER_DENY", "0") != "1",
        reason="set EXPECT_AUTHORIZER_DENY=1 when the caller is NOT IP-allowlisted (CI)",
    )
    def test_denies_request_without_credential(
        self, unauthenticated_client: httpx.Client
    ) -> None:
        r = unauthenticated_client.get("/api/v1/health")
        assert r.status_code in (401, 403), (
            "The API Gateway authorizer allowed a request with no credential from a "
            f"caller that should not be allowlisted — got {r.status_code}. Either "
            "api_allowed_cidrs is wider than intended, or the authorizer is not "
            "attached to this route."
        )

    def test_allows_request_with_ci_key(
        self, client: httpx.Client, ci_headers: dict[str, str]
    ) -> None:
        if not ci_headers:
            pytest.skip("APICURIO_CI_KEY not set — nothing to prove about the header path")
        r = client.get("/api/v1/health")
        assert r.status_code == 200, (
            f"The CI shared-secret header did not authorise this request ({r.status_code}). "
            "The secret container is created by Terraform with no version, so check an "
            "operator has actually set its value: "
            "`aws secretsmanager put-secret-value --secret-id <authorizer_ci_secret_name>`."
        )


# ── CORS ──────────────────────────────────────────────────────────────────────


class TestCorsPreflight:
    """The preflight goes through the AUTHENTICATED client, deliberately.

    A browser preflight cannot carry a custom header — that is exactly why the
    UI's credential path is an allowlisted source IP rather than this secret. So
    this test is not simulating a browser end to end; it is asserting that the
    CORS configuration names the real UI origin, which is the part that silently
    breaks when cors_origins is stale.
    """

    @pytest.mark.skipif(
        not os.getenv("UI_ORIGIN"),
        reason="set UI_ORIGIN to the deployed CloudFront origin",
    )
    def test_preflight_allows_the_ui_origin(self, client: httpx.Client) -> None:
        origin = os.environ["UI_ORIGIN"].rstrip("/")
        r = client.request(
            "OPTIONS",
            "/api/v1/schemas",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        allowed = r.headers.get("access-control-allow-origin", "")
        assert allowed in (origin, "*"), (
            f"Preflight from {origin} was not allowed (Access-Control-Allow-Origin: "
            f"{allowed!r}, status {r.status_code}). cors_origins in envs/<env>.tfvars has "
            "to name the CloudFront domain, which does not exist until the distribution "
            "has been created — so a first deploy needs a second apply to close this. "
            "DEPLOY-DEV.md Phase 4."
        )
