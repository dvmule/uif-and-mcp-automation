"""Thin client for the Apicurio Registry REST API v3, shared by every
script in this reference implementation so the API shape lives in one
place. See ../../../../capabilities/api/guidelines.md §2 for what Apicurio
is and how the group:artifact coordinate model works.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

REGISTRY_URL_ENV = "APICURIO_REGISTRY_URL"


class RegistryNotConfigured(RuntimeError):
    """Raised when APICURIO_REGISTRY_URL is unset — callers should treat
    this as a no-op skip, not a failure (README.md "Why every script
    no-ops instead of failing when unconfigured")."""


@dataclass
class CompatibilityResult:
    compatible: bool
    reason: str | None = None


class ApicurioClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get(REGISTRY_URL_ENV, "")).rstrip("/")
        if not self.base_url:
            raise RegistryNotConfigured(
                f"{REGISTRY_URL_ENV} is not set — skipping registry interaction."
            )
        self.timeout = timeout

    def _versions_url(self, group: str, artifact: str) -> str:
        return f"{self.base_url}/apis/registry/v3/groups/{group}/artifacts/{artifact}/versions"

    def get_latest_content(self, group: str, artifact: str) -> str:
        resp = requests.get(f"{self._versions_url(group, artifact)}/latest/content", timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def get_compatibility_rule(self, group: str, artifact: str, default: str = "BACKWARD") -> str:
        url = f"{self.base_url}/apis/registry/v3/groups/{group}/artifacts/{artifact}/rules/COMPATIBILITY"
        resp = requests.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json().get("config", default)

    def check_compatibility(
        self, group: str, artifact: str, content: str, content_type: str = "application/yaml"
    ) -> CompatibilityResult:
        """Dry-run a candidate version against the artifact's configured
        compatibility rule without persisting it (Apicurio v3 `dryRun` query
        param on the version-create endpoint)."""
        resp = requests.post(
            f"{self._versions_url(group, artifact)}?dryRun=true",
            params={"dryRun": "true"},
            json={"content": {"content": content, "contentType": content_type}},
            timeout=self.timeout,
        )
        if resp.status_code in (200, 201):
            return CompatibilityResult(compatible=True)
        if resp.status_code == 409:
            body: dict[str, Any] = {}
            try:
                body = resp.json()
            except ValueError:
                pass
            return CompatibilityResult(
                compatible=False,
                reason=body.get("detail") or body.get("title") or resp.text,
            )
        resp.raise_for_status()
        return CompatibilityResult(compatible=True)  # pragma: no cover — unreachable, raise_for_status exits first

    def register_version(
        self, group: str, artifact: str, content: str, content_type: str = "application/yaml"
    ) -> dict[str, Any]:
        """Register a real new version. Apicurio content-hashes versions,
        so re-registering unchanged content is a no-op on the registry's
        side — this call is safe to run every merge to main."""
        resp = requests.post(
            self._versions_url(group, artifact),
            json={"content": {"content": content, "contentType": content_type}},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
