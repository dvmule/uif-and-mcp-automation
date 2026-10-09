"""Schema service — business logic for the Schema domain.

Joins data from Apicurio (subjects/versions) and CMDB (ownership) into the
shapes consumed by the API layer. No HTTP, no direct I/O — only orchestrates clients.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.clients.apicurio_client import ApicurioClient, ApicurioError
from app.clients.cmdb_client import CmdbClient
from app.core.logging import get_logger
from app.schemas.overview import OverviewStats
from app.schemas.schema import (
    DataClassification,
    Lifecycle,
    SchemaDetailRead,
    SchemaListResponse,
    SchemaOwner,
    SchemaServiceRef,
    SchemaServicesRead,
    SchemaSummary,
    SchemaVersionRead,
)
from app.services.validation_cache import ValidationCache

logger = get_logger(__name__)


class SchemaService:
    """Service-layer operations for schemas."""

    def __init__(self, apicurio: ApicurioClient, cmdb: CmdbClient) -> None:
        self._apicurio = apicurio
        self._cmdb = cmdb

    async def list_schemas(self, limit: int = 100, offset: int = 0) -> SchemaListResponse:
        """List schemas with parsed metadata block.

        Apicurio v2 /search/artifacts does not include `properties` in its response.
        We fan out a parallel /meta call per artifact to hydrate the properties
        (owner, classification, domain, lifecycle, piiFields) before building summaries.
        A per-artifact hydration failure is logged and skipped — the summary is still
        returned with empty metadata rather than failing the whole list.
        """
        try:
            data = await self._apicurio.search_artifacts(limit=limit, offset=offset)
        except ApicurioError as exc:
            logger.error("schema_service.list.apicurio_error", error=str(exc))
            raise

        artifacts = data.get("artifacts", [])

        if artifacts:
            meta_results = await asyncio.gather(
                *[
                    self._apicurio.get_artifact_metadata(
                        a.get("groupId") or "default", a["id"]
                    )
                    for a in artifacts
                ],
                return_exceptions=True,
            )
            # strict=True: meta_results is an asyncio.gather over artifacts,
            # one result per artifact (exceptions included, via
            # return_exceptions=True), so the lengths always match.
            for artifact, meta in zip(artifacts, meta_results, strict=True):
                if isinstance(meta, Exception):
                    logger.warning(
                        "schema_service.list.meta_hydration_failed",
                        artifact_id=artifact.get("id"),
                        error=str(meta),
                    )
                    continue
                if meta.get("properties"):
                    artifact["properties"] = meta["properties"]
                if meta.get("version"):
                    artifact["version"] = meta["version"]

        items = [self._artifact_to_summary(a) for a in artifacts]

        return SchemaListResponse(
            items=items,
            total=data.get("count", len(items)),
            limit=limit,
            offset=offset,
        )

    async def get_schema_detail(
        self,
        group_id: str,
        artifact_id: str,
        version: str | None = None,
    ) -> SchemaDetailRead:
        """Return full detail for one artifact — content, version list, and metadata."""
        if version:
            meta_coro = self._apicurio.get_artifact_metadata(group_id, artifact_id)
            content_coro = self._apicurio.get_artifact_version_content(
                group_id, artifact_id, version
            )
            versions_coro = self._apicurio.list_artifact_versions(group_id, artifact_id)
            compat_coro = self._apicurio.get_artifact_compatibility(group_id, artifact_id)
        else:
            meta_coro = self._apicurio.get_artifact_metadata(group_id, artifact_id)
            content_coro = self._apicurio.get_artifact_content(group_id, artifact_id)
            versions_coro = self._apicurio.list_artifact_versions(group_id, artifact_id)
            compat_coro = self._apicurio.get_artifact_compatibility(group_id, artifact_id)

        meta, content, raw_versions, compatibility = await asyncio.gather(
            meta_coro, content_coro, versions_coro, compat_coro
        )

        properties = meta.get("properties") or {}

        # Fall back to parsing `metadata` from the schema body if properties lack it.
        if not properties.get("owner.service"):
            try:
                body = json.loads(content)
                embedded = body.get("metadata") or {}
                owner_block = embedded.get("owner") or {}
                if not properties.get("owner.service") and owner_block.get("service"):
                    properties = {**properties, **{
                        "owner.service": owner_block.get("service"),
                        "owner.team": owner_block.get("team"),
                        "owner.email": owner_block.get("email"),
                        "domain": embedded.get("domain") or properties.get("domain"),
                        "dataClassification": (
                            embedded.get("dataClassification")
                            or properties.get("dataClassification")
                        ),
                        "lifecycle": embedded.get("lifecycle") or properties.get("lifecycle"),
                        "piiFields": embedded.get("piiFields") or properties.get("piiFields"),
                    }}
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        resolved_version = version or str(meta.get("version", "latest"))

        versions = [
            SchemaVersionRead(
                version=str(v.get("version", "")),
                state=v.get("state", "UNKNOWN"),
                created_on=v.get("createdOn"),
                created_by=v.get("createdBy"),
            )
            for v in raw_versions
        ]

        return SchemaDetailRead(
            artifact_id=meta.get("id", artifact_id),
            group_id=meta.get("groupId") or group_id,
            name=meta.get("name"),
            type=meta.get("type", "UNKNOWN"),
            state=meta.get("state", "UNKNOWN"),
            created_on=meta.get("createdOn"),
            modified_on=meta.get("modifiedOn"),
            content=content,
            content_version=resolved_version,
            versions=versions,
            owner=SchemaOwner(
                service=properties.get("owner.service"),
                team=properties.get("owner.team"),
                email=properties.get("owner.email"),
            ),
            domain=properties.get("domain"),
            classification=self._parse_classification(properties.get("dataClassification")),
            lifecycle=self._parse_lifecycle(properties.get("lifecycle")),
            pii_fields=self._parse_list(properties.get("piiFields")),
            compatibility=compatibility,
        )

    async def get_schema_services(
        self,
        group_id: str,
        artifact_id: str,
    ) -> SchemaServicesRead:
        """Return services that produce or consume the given schema."""
        producers_cis, consumers_cis = await asyncio.gather(
            self._cmdb.find_producers_of(artifact_id),
            self._cmdb.find_consumers_of(artifact_id),
        )
        return SchemaServicesRead(
            artifact_id=artifact_id,
            group_id=group_id,
            producers=[
                SchemaServiceRef(ci_id=ci.ci_id, urn=ci.urn, name=ci.name, domain=ci.domain)
                for ci in producers_cis
            ],
            consumers=[
                SchemaServiceRef(ci_id=ci.ci_id, urn=ci.urn, name=ci.name, domain=ci.domain)
                for ci in consumers_cis
            ],
        )

    async def get_overview_stats(self, cache: ValidationCache) -> OverviewStats:
        """Aggregate registry stats for the Overview tab.

        Runs three independent fetches in parallel where possible:
        - total_schemas: count from Apicurio search (single request)
        - schemas_with_compatibility_rules: fan-out per artifact (N parallel requests)
        - non_compliant_schemas: from validation cache (populates lazily if empty)
        """
        from app.services.metadata_validator import populate_validation_cache

        count_coro = self._apicurio.search_artifacts(limit=1, offset=0)
        all_coro = self._apicurio.search_artifacts(limit=500, offset=0)

        count_data, all_data = await asyncio.gather(count_coro, all_coro)

        total = count_data.get("count", 0)
        artifacts = all_data.get("artifacts", [])

        compat_results = await asyncio.gather(
            *[
                self._apicurio.get_artifact_compatibility(
                    a.get("groupId") or "default", a["id"]
                )
                for a in artifacts
            ],
            return_exceptions=True,
        )
        compat_count = sum(1 for r in compat_results if isinstance(r, str))

        if cache.is_empty():
            await populate_validation_cache(self._apicurio, self._cmdb, cache)
        non_compliant = sum(1 for r in cache.get_all() if not r.passed)

        return OverviewStats(
            total_schemas=total,
            schemas_with_compatibility_rules=compat_count,
            non_compliant_schemas=non_compliant,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _artifact_to_summary(self, artifact: dict[str, Any]) -> SchemaSummary:
        """Build a SchemaSummary from an Apicurio artifact search result.

        Apicurio v2 search returns artifacts with:
          id, groupId, type, state, createdOn, modifiedOn, name, description,
          labels (list), properties (dict)

        We pull the metadata block from `properties` if present. If the metadata is
        instead embedded in the schema body, the detail endpoint will parse it.
        """
        properties = artifact.get("properties") or {}

        owner = SchemaOwner(
            service=properties.get("owner.service"),
            team=properties.get("owner.team"),
            email=properties.get("owner.email"),
        )

        classification = self._parse_classification(properties.get("dataClassification"))
        lifecycle = self._parse_lifecycle(properties.get("lifecycle"))
        pii_fields = self._parse_list(properties.get("piiFields"))

        return SchemaSummary(
            artifact_id=artifact["id"],
            group_id=artifact.get("groupId") or "default",
            name=artifact.get("name"),
            type=artifact.get("type", "UNKNOWN"),
            state=artifact.get("state", "UNKNOWN"),
            created_on=artifact.get("createdOn"),
            modified_on=artifact.get("modifiedOn"),
            latest_version=str(artifact["version"]) if artifact.get("version") else None,
            owner=owner,
            domain=properties.get("domain"),
            classification=classification,
            lifecycle=lifecycle,
            pii_fields=pii_fields,
        )

    @staticmethod
    def _parse_classification(value: str | None) -> DataClassification | None:
        if not value:
            return None
        try:
            return DataClassification(value.lower())
        except ValueError:
            logger.warning("schema_service.classification.invalid", value=value)
            return None

    @staticmethod
    def _parse_lifecycle(value: str | None) -> Lifecycle | None:
        if not value:
            return None
        try:
            return Lifecycle(value.lower())
        except ValueError:
            return None

    @staticmethod
    def _parse_list(value: Any) -> list[str]:
        """Apicurio properties are strings — JSON-encode lists when registering.

        Accept both a JSON-encoded list and a comma-separated string for resilience.
        """
        if not value:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
        return [v.strip() for v in str(value).split(",") if v.strip()]
