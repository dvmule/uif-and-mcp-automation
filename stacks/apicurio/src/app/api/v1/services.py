"""Service endpoints.

HTTP only. All business logic lives in `services/portfolio_service.py` and
`services/onboarding_service.py`.

URN routing: URNs contain colons (urn:pearson:service:commerce:catalog-svc).
The UI URL-encodes them before embedding in the path, so they arrive here as a
single decoded path segment. No :path converter needed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.clients.apicurio_client import ApicurioClient
from app.clients.cmdb_client import CmdbClient
from app.core.dependencies import get_apicurio_client, get_cmdb, get_onboarding_service
from app.schemas.service import OnboardManifest, OnboardResult, ServicePortfolioRead, ServiceSummary
from app.services.onboarding_service import OnboardingService
from app.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/services", tags=["services"])


def get_portfolio_service(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
    cmdb: CmdbClient = Depends(get_cmdb),
) -> PortfolioService:
    return PortfolioService(apicurio=apicurio, cmdb=cmdb)


@router.get("", response_model=list[ServiceSummary])
async def list_services(
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[ServiceSummary]:
    """Return a summary list of all active services."""
    return await service.list_services()


@router.post("/onboard", response_model=OnboardResult)
async def simulate_onboard(
    body: OnboardManifest,
    service: OnboardingService = Depends(get_onboarding_service),
) -> OnboardResult:
    """Simulate deploying a service by validating its base image manifest against the registry."""
    return await service.simulate_deploy(body)


@router.get("/{urn}/schema-portfolio", response_model=ServicePortfolioRead)
async def get_schema_portfolio(
    urn: str,
    service: PortfolioService = Depends(get_portfolio_service),
) -> ServicePortfolioRead:
    """Return all schemas produced and consumed by the given service URN."""
    result = await service.get_portfolio(urn)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown service URN: {urn}")
    return result
