"""API v1 router — assembles all v1 endpoints."""

from fastapi import APIRouter

from app.api.v1 import delivery_lab, health, schemas, services

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(schemas.router)
api_router.include_router(services.router)
api_router.include_router(delivery_lab.router)
