"""Schema endpoints.

HTTP only. All business logic lives in the services layer.
"""

from fastapi import APIRouter, Depends, Query

from app.clients.apicurio_client import ApicurioClient
from app.clients.cmdb_client import CmdbClient
from app.core.dependencies import (
    get_apicurio_client,
    get_cmdb,
    get_onboarding_service,
    get_schema_service,
)
from app.schemas.overview import OverviewStats
from app.schemas.schema import SchemaDetailRead, SchemaListResponse, SchemaServicesRead
from app.schemas.service import (
    DirectSchemaRegisterRequest,
    SchemaRegistrationInput,
    SchemaRegistrationResult,
)
from app.schemas.validation import NonCompliantResponse
from app.services.metadata_validator import populate_validation_cache
from app.services.onboarding_service import OnboardingService
from app.services.schema_service import SchemaService
from app.services.validation_cache import ValidationCache, get_validation_cache

router = APIRouter(prefix="/schemas", tags=["schemas"])


# `""` and not `"/"`, matching services.py:32's `@router.get("")`. Under a
# router prefix, `"/"` registers the collection as `/api/v1/schemas/` and makes
# the trailing slash part of the contract -- which made this the only
# bare-slash route in the API while every sibling carried an explicit segment.
# Asking for `/api/v1/schemas` then got a 307 from Starlette's redirect_slashes
# rather than a response, and tier 2 read that 307 as a broken deploy.
@router.get("", response_model=SchemaListResponse)
async def list_schemas(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: SchemaService = Depends(get_schema_service),
) -> SchemaListResponse:
    """List all schemas in the registry with parsed metadata block."""
    return await service.list_schemas(limit=limit, offset=offset)


@router.get("/non-compliant", response_model=NonCompliantResponse)
async def get_non_compliant(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
    cmdb: CmdbClient = Depends(get_cmdb),
    cache: ValidationCache = Depends(get_validation_cache),
) -> NonCompliantResponse:
    """Return schemas failing one or more metadata validation rules.

    Lazily populates the validation cache on first call. Subsequent calls
    return cached results until POST /non-compliant/refresh is called.
    """
    if cache.is_empty():
        await populate_validation_cache(apicurio, cmdb, cache)
    return _build_response(cache)


@router.post("/non-compliant/refresh", response_model=NonCompliantResponse)
async def refresh_non_compliant(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
    cmdb: CmdbClient = Depends(get_cmdb),
    cache: ValidationCache = Depends(get_validation_cache),
) -> NonCompliantResponse:
    """Clear the validation cache and re-run a full scan.

    Returns the fresh results immediately. Use this after registering or
    updating schemas to see up-to-date compliance state.
    """
    await populate_validation_cache(apicurio, cmdb, cache)
    return _build_response(cache)


@router.get("/overview", response_model=OverviewStats)
async def get_overview(
    service: SchemaService = Depends(get_schema_service),
    cache: ValidationCache = Depends(get_validation_cache),
) -> OverviewStats:
    """Return aggregate registry stats for the Overview tab."""
    return await service.get_overview_stats(cache)


@router.post("/{group_id}/{artifact_id}", response_model=SchemaRegistrationResult)
async def register_schema(
    group_id: str,
    artifact_id: str,
    body: DirectSchemaRegisterRequest,
    svc: OnboardingService = Depends(get_onboarding_service),
) -> SchemaRegistrationResult:
    """Register a single schema for a produces topic (post-deploy inline flow)."""
    reg_input = SchemaRegistrationInput(
        content=body.content,
        type_override=body.type_override,
        data_classification=body.data_classification,
        pii_fields=body.pii_fields,
        lifecycle=body.lifecycle,
    )
    return await svc.register_single_schema(group_id, artifact_id, body.service_urn, reg_input)


@router.get("/{group_id}/{artifact_id}/services", response_model=SchemaServicesRead)
async def get_schema_services(
    group_id: str,
    artifact_id: str,
    service: SchemaService = Depends(get_schema_service),
) -> SchemaServicesRead:
    """Return services that produce or consume the given schema."""
    return await service.get_schema_services(group_id, artifact_id)


@router.get("/{group_id}/{artifact_id}", response_model=SchemaDetailRead)
async def get_schema_detail(
    group_id: str,
    artifact_id: str,
    version: str | None = Query(default=None, description="Version to fetch; omit for latest"),
    service: SchemaService = Depends(get_schema_service),
) -> SchemaDetailRead:
    """Return full detail for one schema — content, version history, and metadata."""
    return await service.get_schema_detail(group_id, artifact_id, version=version)


def _build_response(cache: ValidationCache) -> NonCompliantResponse:
    all_results = cache.get_all()
    failing = [r for r in all_results if not r.passed]
    return NonCompliantResponse(
        items=failing,
        last_validated=cache.populated_at,
        total_checked=len(all_results),
    )
