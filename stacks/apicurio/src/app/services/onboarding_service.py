"""Onboarding service — simulates a service deploy via a base image manifest.

Validates each declared topic against Apicurio, optionally registers new schemas
for produces topics, and returns a readiness report. Demonstrates the FSTA
concept that schemas reach the platform via the base image contract.

Producer/consumer edges were also written into the g:live RDF graph here until
the Oxigraph removal (ADR-0007); the CMDB manifest update below is now the only
record of the produces/consumes relationship.
"""

from __future__ import annotations

import asyncio

from app.clients.apicurio_client import ApicurioClient, ApicurioError
from app.clients.cmdb_client import CmdbCi, CmdbClient
from app.core.logging import get_logger
from app.schemas.service import (
    OnboardManifest,
    OnboardResult,
    SchemaRegistrationInput,
    SchemaRegistrationResult,
    TopicReadiness,
)
from app.services.metadata_injector import build_apicurio_properties, inject_metadata
from app.services.schema_type_detector import detect_schema_type

logger = get_logger(__name__)

_DEFAULT_GROUP = "default"


class OnboardingService:
    def __init__(
        self,
        apicurio: ApicurioClient,
        cmdb: CmdbClient,
    ) -> None:
        self._apicurio = apicurio
        self._cmdb = cmdb

    async def simulate_deploy(self, manifest: OnboardManifest) -> OnboardResult:
        ci = await self._cmdb.resolve_urn(manifest.service_urn)

        # Register schemas for produces topics that carry a payload
        registrations: list[SchemaRegistrationResult] = []
        if manifest.schema_registrations and ci is not None:
            for topic, reg_input in manifest.schema_registrations.items():
                if topic not in manifest.produces:
                    continue
                result = await self._register_schema(_DEFAULT_GROUP, topic, reg_input, ci)
                registrations.append(result)

        produces_results, consumes_results = await asyncio.gather(
            self._check_topics(manifest.produces, role="produces"),
            self._check_topics(manifest.consumes, role="consumes"),
        )

        # Flag topics that were registered in this request
        just_registered = {r.topic for r in registrations if r.registered}
        produces_results = [
            tr.model_copy(update={"just_registered": True})
            if tr.topic in just_registered
            else tr
            for tr in produces_results
        ]

        if ci is not None:
            await self._cmdb.update_manifest(
                manifest.service_urn, manifest.produces, manifest.consumes
            )
            logger.info(
                "onboarding.cmdb_manifest_updated",
                urn=manifest.service_urn,
                ci_id=ci.ci_id,
            )

        return OnboardResult(
            service_urn=manifest.service_urn,
            ci_id=ci.ci_id if ci else None,
            name=ci.name if ci else None,
            produces=produces_results,
            consumes=consumes_results,
            registrations=registrations,
        )

    async def register_single_schema(
        self,
        group_id: str,
        artifact_id: str,
        service_urn: str,
        reg_input: SchemaRegistrationInput,
    ) -> SchemaRegistrationResult:
        """Register a single schema directly (used by the post-deploy inline flow)."""
        ci = await self._cmdb.resolve_urn(service_urn)
        if ci is None:
            return SchemaRegistrationResult(
                topic=artifact_id,
                registered=False,
                error=f"Unknown service URN: {service_urn}",
            )
        result = await self._register_schema(group_id, artifact_id, reg_input, ci)
        return result

    async def _register_schema(
        self,
        group_id: str,
        artifact_id: str,
        reg_input: SchemaRegistrationInput,
        ci: CmdbCi,
    ) -> SchemaRegistrationResult:
        schema_type = reg_input.type_override or detect_schema_type(reg_input.content)
        try:
            enriched = inject_metadata(reg_input.content, schema_type, ci, reg_input)
            props = build_apicurio_properties(ci, reg_input)
            await self._apicurio.create_artifact(
                group_id, artifact_id, enriched, schema_type, properties=props
            )
            logger.info(
                "onboarding.schema_registered",
                topic=artifact_id,
                schema_type=schema_type,
            )
            return SchemaRegistrationResult(
                topic=artifact_id, registered=True, schema_type=schema_type
            )
        except Exception as exc:
            logger.warning(
                "onboarding.schema_registration_failed",
                topic=artifact_id,
                error=str(exc),
            )
            return SchemaRegistrationResult(
                topic=artifact_id, registered=False, error=str(exc)
            )

    async def _check_topics(
        self, topics: list[str], *, role: str
    ) -> list[TopicReadiness]:
        tasks = [self._check_one(topic, role=role) for topic in topics]
        return list(await asyncio.gather(*tasks))

    async def _check_one(self, artifact_id: str, *, role: str) -> TopicReadiness:
        try:
            meta = await self._apicurio.get_artifact_metadata(_DEFAULT_GROUP, artifact_id)
            return TopicReadiness(
                topic=artifact_id,
                registered=True,
                schema_type=meta.get("type"),
            )
        except ApicurioError as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return TopicReadiness(
                    topic=artifact_id,
                    registered=False,
                    drift="D6" if role == "produces" else None,
                )
            logger.warning("onboarding.topic_check_error", topic=artifact_id, error=str(exc))
            return TopicReadiness(topic=artifact_id, registered=False)
