"""FastAPI dependency providers.

Centralises how clients and services are constructed so route handlers stay clean.
"""

from collections.abc import AsyncIterator

from fastapi import Depends

from app.clients.apicurio_client import ApicurioClient
from app.clients.cmdb_client import CmdbClient, get_cmdb_client
from app.services.onboarding_service import OnboardingService
from app.services.schema_service import SchemaService


async def get_apicurio_client() -> AsyncIterator[ApicurioClient]:
    """Yield an Apicurio client scoped to the request lifecycle."""
    client = ApicurioClient()
    try:
        yield client
    finally:
        await client.close()


def get_cmdb() -> CmdbClient:
    """Return the configured CMDB client (always the mock — see cmdb_client)."""
    return get_cmdb_client()


def get_schema_service(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
    cmdb: CmdbClient = Depends(get_cmdb),
) -> SchemaService:
    return SchemaService(apicurio=apicurio, cmdb=cmdb)


def get_onboarding_service(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
    cmdb: CmdbClient = Depends(get_cmdb),
) -> OnboardingService:
    return OnboardingService(apicurio=apicurio, cmdb=cmdb)
