"""FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.clients.apicurio_client import ApicurioClient
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks.

    Configures logging and holds the Apicurio client for the process lifetime.

    There are no background tasks. The RDF extractors (live + mapping) and the
    CMDB graph sync ran here until the Oxigraph removal — see ADR-0007.
    """
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()
    logger.info("app.startup", apicurio=settings.apicurio_api_url)

    apicurio = ApicurioClient()

    try:
        yield
    finally:
        await apicurio.close()
        logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Pearson Schema Registry BFF",
        version="0.1.0",
        description=(
            "Backend-for-Frontend serving the custom schema UI. Joins Apicurio "
            "Registry and CMDB into purpose-built endpoints."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    return app


app = create_app()
