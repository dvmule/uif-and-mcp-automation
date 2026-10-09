"""Apicurio Registry v3 client.

Wraps the v3 REST API at /apis/registry/v3. All Apicurio I/O goes through this
client — no other module imports httpx and calls Apicurio directly.

Ported from the v2 client in UIFPE-36, when this stack moved to the
`apicurio/apicurio-registry:3.3.2` image. Verified against the upstream
OpenAPI document for tag v3.3.2, not from memory:
https://github.com/Apicurio/apicurio-registry/blob/v3.3.2/common/src/main/resources/META-INF/openapi.json

## v2 → v3 differences this client absorbs

The wire shape changed in ways that would otherwise ripple through every
service. Because `clients/` is the only layer permitted to know an external
system's shape, the translation happens here and the internal shape the
services already consume is preserved verbatim:

| Internal key (unchanged) | v2 wire | v3 wire        |
|--------------------------|---------|----------------|
| `id`                     | `id`    | `artifactId`   |
| `type`                   | `type`  | `artifactType` |
| `properties`             | `properties` | `labels`  |

Endpoint and payload changes, all handled below:

- Artifact metadata moved off `/meta`: `GET /groups/{g}/artifacts/{a}` now
  returns it directly.
- Content is no longer served from the artifact path. It is
  `GET /groups/{g}/artifacts/{a}/versions/{versionExpression}/content`, where
  `versionExpression` is either a literal version or `branch={branchId}`. The
  latest version is therefore `branch=latest`, not an implicit default.
- `POST /groups/{g}/artifacts` takes a JSON `CreateArtifact` body. The
  `X-Registry-ArtifactId` / `X-Registry-ArtifactType` headers v2 used are gone.
- `ifExists` no longer accepts `RETURN_OR_UPDATE`. The v3 enum is `FAIL`,
  `CREATE_VERSION`, `FIND_OR_CREATE_VERSION`; the idempotent option is
  `FIND_OR_CREATE_VERSION`.
- v2's flat `properties` map and its separate `labels` list were consolidated
  into one `labels` map of string → string. The ADR-0001 derived index is
  therefore stored in v3 `labels`. The rule id `properties_mismatch` is
  deliberately left unchanged — it is a published contract the UI and the
  governance docs key on, and renaming it would break them to describe an
  internal transport detail.
- Health is not part of the registry API, and in 3.3.2 it is not reachable on
  the traffic port at all: Quarkus serves readiness on the **management
  interface, port 9000, at `/health/ready`**. Every path under `/q/` returns 404
  on both 8080 and 9000. Since the BFF reaches the registry through the ALB on
  the traffic port, it cannot see 9000 — so `is_reachable()` probes the v3 API's
  own `system/info` instead. See its docstring.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The artifact branch v3 keeps pointing at the newest version. Used as a
# versionExpression wherever this client wants "the current content".
_LATEST = "branch=latest"


class ApicurioError(Exception):
    """Raised when Apicurio returns an error response or is unreachable."""


def _normalise_artifact(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a v3 artifact or version object onto the internal shape.

    Additive: the v3 keys are left in place so anything that wants the real
    field names still has them. Only the three renamed keys are back-filled,
    and only when absent, so this is safe to apply to an already-normalised
    dict.
    """
    out = dict(raw)
    if "id" not in out and "artifactId" in out:
        out["id"] = out["artifactId"]
    if "type" not in out and "artifactType" in out:
        out["type"] = out["artifactType"]
    if "properties" not in out and "labels" in out:
        out["properties"] = out["labels"] or {}
    return out


class ApicurioClient:
    """Async client for the Apicurio Registry v3 REST API.

    Lifecycle: instantiate once per request via the FastAPI dependency
    `get_apicurio_client` in app.core.dependencies. Always use `async with` or
    close explicitly.
    """

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        self._api_url = base_url or settings.apicurio_api_url
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            timeout=timeout or settings.apicurio_request_timeout_seconds,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> ApicurioClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def is_reachable(self) -> bool:
        """Return True if the registry answers on its own v3 API.

        Probes `system/info` rather than a Quarkus health endpoint. Verified
        against apicurio/apicurio-registry:3.3.2: readiness is served on the
        management interface (port 9000) at /health/ready, not at
        /q/health/ready, and not on the traffic port at all. The BFF reaches the
        registry through the ALB on the traffic port and cannot see port 9000,
        so the API's own `system/info` is both reachable and the stronger
        signal — it proves the v3 API answers, not merely that the JVM started.

        The ECS container health check and the ALB target group probe
        9000/health/ready directly, which they can; this method deliberately
        does not, and the two are no longer the same check.
        """
        # Uses this instance's own API URL, not settings, so a client
        # constructed against another host probes that host.
        url = f"{self._api_url.rstrip('/')}/system/info"
        try:
            async with httpx.AsyncClient(timeout=3.0) as probe:
                resp = await probe.get(url)
                return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("apicurio.health.unreachable", url=url, error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Artifacts (subjects)
    # ------------------------------------------------------------------

    async def search_artifacts(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Search artifacts.

        Returns `{"artifacts": [...], "count": n}` with each artifact
        normalised to the internal shape. The `group` argument keeps its name
        for callers; v3 spells the query parameter `groupId`.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if group:
            params["groupId"] = group
        data = await self._get("/search/artifacts", params=params)
        artifacts = [_normalise_artifact(a) for a in data.get("artifacts", [])]
        return {"artifacts": artifacts, "count": data.get("count", len(artifacts))}

    async def get_artifact_metadata(
        self,
        group_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        """Get artifact metadata.

        v3 serves this from the artifact path itself — the v2 `/meta` suffix no
        longer exists.
        """
        data = await self._get(f"/groups/{group_id}/artifacts/{artifact_id}")
        return _normalise_artifact(data)

    async def get_artifact_content(
        self,
        group_id: str,
        artifact_id: str,
    ) -> str:
        """Get the latest version's content as raw text.

        v3 has no "content of the artifact" endpoint; content belongs to a
        version, so this resolves the `latest` branch explicitly.
        """
        return await self.get_artifact_version_content(group_id, artifact_id, _LATEST)

    async def list_artifact_versions(
        self,
        group_id: str,
        artifact_id: str,
    ) -> list[dict[str, Any]]:
        """List all versions of an artifact, newest-first ordering as returned."""
        data = await self._get(f"/groups/{group_id}/artifacts/{artifact_id}/versions")
        return [_normalise_artifact(v) for v in data.get("versions", [])]

    async def get_artifact_version_content(
        self,
        group_id: str,
        artifact_id: str,
        version: str,
    ) -> str:
        """Get raw content for one version.

        `version` is a v3 versionExpression: a literal version id, or
        `branch={branchId}`. Callers passing a bare version number are
        unaffected by the v3 change.
        """
        url = f"/groups/{group_id}/artifacts/{artifact_id}/versions/{version}/content"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise ApicurioError(f"Failed to fetch version content: {exc}") from exc

    async def get_artifact_compatibility(
        self,
        group_id: str,
        artifact_id: str,
    ) -> str | None:
        """Return the COMPATIBILITY rule config string, or None if no rule is set."""
        try:
            data = await self._get(
                f"/groups/{group_id}/artifacts/{artifact_id}/rules/COMPATIBILITY"
            )
            config: str | None = data.get("config")
            return config
        except ApicurioError as exc:
            # No rule configured is a 404 and a legitimate answer, not a failure.
            if "404" in str(exc):
                return None
            raise

    async def create_artifact(
        self,
        group_id: str,
        artifact_id: str,
        content: str,
        schema_type: str,
        properties: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create the artifact, or add a version to it if it already exists.

        `ifExists=FIND_OR_CREATE_VERSION` is v3's idempotent option, replacing
        v2's `RETURN_OR_UPDATE`. The artifact id and type move from headers into
        the JSON body, and the first version's content is nested under
        `firstVersion.content`.

        `properties` keeps its parameter name for callers and is written to v3
        `labels` — the ADR-0001 derived index.
        """
        body: dict[str, Any] = {
            "artifactId": artifact_id,
            "artifactType": schema_type,
            "firstVersion": {
                "content": {
                    "content": content,
                    "contentType": "application/json",
                }
            },
        }
        if properties:
            # Set on create so the index is never briefly absent. Also re-sent
            # via _put_artifact_meta below, which is what updates it when the
            # artifact already existed and this call only added a version.
            body["labels"] = properties

        try:
            resp = await self._client.post(
                f"/groups/{group_id}/artifacts",
                json=body,
                params={"ifExists": "FIND_OR_CREATE_VERSION"},
            )
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "apicurio.create_artifact.error",
                artifact_id=artifact_id,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            raise ApicurioError(
                f"Apicurio returned {exc.response.status_code} creating {artifact_id}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApicurioError(f"Transport error creating {artifact_id}: {exc}") from exc

        if properties:
            await self._put_artifact_meta(group_id, artifact_id, properties)

        # v3 wraps the result as {"artifact": ArtifactMetaData, "version": ...}.
        # Unwrap to the artifact so callers keep seeing a flat object with `id`.
        artifact = payload.get("artifact") or payload
        result = _normalise_artifact(artifact)
        if "version" in payload:
            result["version"] = payload["version"]
        return result

    async def _put_artifact_meta(
        self, group_id: str, artifact_id: str, properties: dict[str, str]
    ) -> None:
        """Write the derived index to v3 `labels` (ADR-0001).

        Best-effort by design: the schema body is authoritative, so a failure
        here leaves the index stale rather than the truth wrong. A stale index
        is what the `properties_mismatch` validation rule exists to catch.
        """
        try:
            resp = await self._client.put(
                f"/groups/{group_id}/artifacts/{artifact_id}",
                json={"labels": properties},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("apicurio.put_meta.failed", artifact_id=artifact_id, error=str(exc))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "apicurio.http_error",
                path=path,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            raise ApicurioError(
                f"Apicurio returned {exc.response.status_code} for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("apicurio.transport_error", path=path, error=str(exc))
            raise ApicurioError(f"Apicurio transport error on {path}: {exc}") from exc
