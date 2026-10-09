"""Portfolio service — business logic for the Service domain.

Joins CMDB ownership data with the schema list to answer:
  "What does this service produce? What does it consume?"

No HTTP, no direct I/O — orchestrates ApicurioClient and CmdbClient
via SchemaService.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.clients.apicurio_client import ApicurioClient
from app.clients.cmdb_client import CmdbCi, CmdbClient
from app.core.logging import get_logger
from app.schemas.service import (
    ServiceDependencies,
    ServiceDependency,
    ServicePortfolioRead,
    ServiceSummary,
)
from app.services.schema_service import SchemaService

logger = get_logger(__name__)


async def _async_none() -> None:
    return None


class PortfolioService:
    """Service-layer operations for the service portfolio query."""

    def __init__(self, apicurio: ApicurioClient, cmdb: CmdbClient) -> None:
        self._schema_svc = SchemaService(apicurio=apicurio, cmdb=cmdb)
        self._cmdb = cmdb

    async def list_services(self) -> list[ServiceSummary]:
        """Return a summary of all active services from CMDB."""
        cis = await self._cmdb.list_active()
        app_lookups = await asyncio.gather(*[
            self._cmdb.get_application(ci.app_id) if ci.app_id else _async_none()
            for ci in cis
        ])
        return [
            ServiceSummary(
                ci_id=ci.ci_id,
                urn=ci.urn,
                name=ci.name,
                domain=ci.domain,
                produces_count=len(ci.produces),
                consumes_count=len(ci.consumes),
                produces=list(ci.produces),
                consumes=list(ci.consumes),
                app_id=ci.app_id,
                app_name=app.name if app else None,
            )
            # strict=True: app_lookups is an asyncio.gather over a comprehension
            # of cis, so it has exactly one entry per ci by construction.
            for ci, app in zip(cis, app_lookups, strict=True)
        ]

    async def get_portfolio(self, urn: str) -> ServicePortfolioRead | None:
        """Return the schema portfolio for a service URN, or None if unknown."""
        ci = await self._cmdb.resolve_urn(urn)
        if ci is None:
            logger.warning("portfolio_service.unknown_urn", urn=urn)
            return None

        schemas = await self._schema_svc.list_schemas(limit=500)

        produces = [s for s in schemas.items if s.artifact_id in set(ci.produces)]
        consumes = [s for s in schemas.items if s.artifact_id in set(ci.consumes)]

        registered_produces = [s.artifact_id for s in produces]
        registered_consumes = [s.artifact_id for s in consumes]

        dependencies = await self._compute_dependencies(
            ci, registered_produces, registered_consumes
        )

        logger.info(
            "portfolio_service.get_portfolio",
            urn=urn,
            ci_id=ci.ci_id,
            produces=len(produces),
            consumes=len(consumes),
        )

        app = await self._cmdb.get_application(ci.app_id) if ci.app_id else None

        return ServicePortfolioRead(
            ci_id=ci.ci_id,
            urn=ci.urn,
            name=ci.name,
            domain=ci.domain,
            produces=produces,
            consumes=consumes,
            dependencies=dependencies,
            app_id=ci.app_id,
            app_name=app.name if app else None,
        )

    async def _compute_dependencies(
        self,
        ci: CmdbCi,
        registered_produces: list[str],
        registered_consumes: list[str],
    ) -> ServiceDependencies:
        """Compute upstream and downstream service dependencies for a CI.

        Uses only registry-registered topics so dependency edges reflect actual
        schema contracts, not unregistered CMDB intent (D6 topics are excluded).

        Downstream: services that consume at least one schema this service produces.
        Upstream: services that produce at least one schema this service consumes.
        Deduplicates by ci_id and sums shared_schema_count.
        """
        downstream_counts: dict[str, tuple[CmdbCi, int]] = defaultdict(lambda: (None, 0))  # type: ignore[assignment]
        upstream_counts: dict[str, tuple[CmdbCi, int]] = defaultdict(lambda: (None, 0))  # type: ignore[assignment]

        downstream_tasks = [
            self._cmdb.find_consumers_of(subject) for subject in registered_produces
        ]
        upstream_tasks = [self._cmdb.find_producers_of(subject) for subject in registered_consumes]

        downstream_results = await asyncio.gather(*downstream_tasks)
        upstream_results = await asyncio.gather(*upstream_tasks)

        for related_cis in downstream_results:
            for related in related_cis:
                if related.ci_id == ci.ci_id:
                    continue
                prev_ci, prev_count = downstream_counts[related.ci_id]
                downstream_counts[related.ci_id] = (
                    related if prev_ci is None else prev_ci,
                    prev_count + 1,
                )

        for related_cis in upstream_results:
            for related in related_cis:
                if related.ci_id == ci.ci_id:
                    continue
                prev_ci, prev_count = upstream_counts[related.ci_id]
                upstream_counts[related.ci_id] = (
                    related if prev_ci is None else prev_ci,
                    prev_count + 1,
                )

        def _to_dep(entry: tuple[CmdbCi, int]) -> ServiceDependency:
            related_ci, count = entry
            return ServiceDependency(
                ci_id=related_ci.ci_id,
                urn=related_ci.urn,
                name=related_ci.name,
                domain=related_ci.domain,
                shared_schema_count=count,
            )

        return ServiceDependencies(
            downstream=sorted(
                [_to_dep(v) for v in downstream_counts.values()],
                key=lambda d: -d.shared_schema_count,
            ),
            upstream=sorted(
                [_to_dep(v) for v in upstream_counts.values()],
                key=lambda d: -d.shared_schema_count,
            ),
        )
