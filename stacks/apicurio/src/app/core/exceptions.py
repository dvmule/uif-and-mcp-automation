"""Custom exceptions and FastAPI exception handlers.

Services raise these. Route handlers don't catch them — the registered handler
maps them to HTTP responses.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.clients.apicurio_client import ApicurioError
from app.core.logging import get_logger

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI app."""

    @app.exception_handler(ApicurioError)
    async def _apicurio_error(request: Request, exc: ApicurioError) -> JSONResponse:
        logger.error("apicurio.error", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=502,
            content={
                "error": "apicurio_unavailable",
                "message": "Failed to communicate with the Apicurio Registry.",
                "detail": str(exc),
            },
        )
