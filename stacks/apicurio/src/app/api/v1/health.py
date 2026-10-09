"""Health endpoint.

Reports liveness plus reachability of Apicurio, so the response reflects real
downstream reachability — not just the BFF process state.

Apicurio is the only downstream dependency checked. Oxigraph was the second
until it was removed (ADR-0007).
"""

from fastapi import APIRouter, Depends

from app.clients.apicurio_client import ApicurioClient
from app.core.dependencies import get_apicurio_client
from app.schemas.health import DependencyHealth, HealthResponse, HealthState

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    apicurio: ApicurioClient = Depends(get_apicurio_client),
) -> HealthResponse:
    """Liveness + readiness check including downstream dependency reachability."""
    apicurio_up = await apicurio.is_reachable()

    deps = [
        DependencyHealth(
            name="apicurio",
            state=HealthState.UP if apicurio_up else HealthState.DOWN,
            detail=None if apicurio_up else "Apicurio /health did not respond",
        ),
    ]

    overall = (
        HealthState.UP
        if all(d.state == HealthState.UP for d in deps)
        else HealthState.DEGRADED
    )

    return HealthResponse(state=overall, version="0.1.0", dependencies=deps)
